"""TripService Orchestrator and Conversation State Machine for TripCaspian.

Handles multi-turn conversational flows, provider fan-out, optimization ranking,
auto-booking scheduling handoffs, and seat watching.
"""

import logging
import re
from typing import Any
from tripcaspian.storage import SQLiteStorage
from tripcaspian.intake import parse_trip_request, generate_followup_question, TripQuery
from tripcaspian.optimizer import rank_route_options, RankedOption
from tripcaspian.providers import IRCTCProvider, RedBusProvider, CabProvider, RouteOption
from tripcaspian.scheduler import BookingScheduler
from tripcaspian.watcher import AvailabilityWatcher

logger = logging.getLogger(__name__)


class TripService:
    """Service layer orchestrator for TripCaspian."""

    def __init__(
        self,
        storage: SQLiteStorage | None = None,
        scheduler: BookingScheduler | None = None,
        watcher: AvailabilityWatcher | None = None,
        caspian_client: Any = None,
    ):
        self.storage = storage or SQLiteStorage()
        self.scheduler = scheduler or BookingScheduler()
        self.watcher = watcher or AvailabilityWatcher(storage=self.storage, caspian_client=caspian_client)
        self.client = caspian_client

        # Register providers
        self.providers = [
            IRCTCProvider(),
            RedBusProvider(),
            CabProvider(),
        ]

    def set_caspian_client(self, client: Any) -> None:
        """Bind Caspian SDK client instance."""
        self.client = client
        self.watcher.client = client

    def handle_user_message(
        self, conversation_id: str, sender: dict | None, text: str
    ) -> str:
        """Main entry point for processing incoming messages from any channel."""
        clean_text = text.strip()
        lower_text = clean_text.lower()

        # Intercept explicit cancel requests
        if "cancel" in lower_text:
            return self._handle_cancellation(conversation_id)

        # Retrieve or initialize session
        session = self.storage.get_session(conversation_id)
        if not session:
            state_name = "NEW"
            collected_fields = {}
        else:
            state_name = session["state_name"]
            collected_fields = session["collected_fields"]

        # Handle Option Selection ("book option 1", "option 2", "book 1", "1")
        if state_name in ("RESULTS_SHOWN", "OPTION_SELECTED") and re.search(r'\b(?:book\s+)?(?:option\s+)?([1-9])\b', lower_text):
            match = re.search(r'\b(?:book\s+)?(?:option\s+)?([1-9])\b', lower_text)
            opt_num = int(match.group(1))
            return self._handle_option_selection(conversation_id, collected_fields, opt_num, clean_text)

        # Handle Delay Scheduling Request ("book in 30 min", "in 1 hour", "auto-book in 10 minutes")
        delay_match = re.search(r'(?:book|auto-book|schedule)?\s*in\s*(\d+)\s*(min|minute|mins|minutes|hour|hours|h|m)', lower_text)
        if delay_match and state_name == "OPTION_SELECTED":
            amount = int(delay_match.group(1))
            unit = delay_match.group(2)
            seconds = amount * 60 if 'm' in unit or 'min' in unit else amount * 3600
            return self._schedule_delayed_booking(conversation_id, session, seconds)

        # Handle Immediate Booking Request ("book now", "confirm")
        if state_name == "OPTION_SELECTED" and ("now" in lower_text or "confirm" in lower_text or "yes" in lower_text):
            return self.execute_booking_handoff(conversation_id)

        # Intake Parsing for travel inquiry parameters
        query = parse_trip_request(clean_text, existing=collected_fields)
        updated_fields = query.to_dict()

        # Check if inquiry is complete
        if not query.is_complete():
            self.storage.save_session(conversation_id, "COLLECTING", updated_fields)
            followup = generate_followup_question(query)
            return followup or "Please specify your trip source, destination, and budget."

        # Complete Query: Execute Provider Search & Ranking
        self.storage.save_session(conversation_id, "READY_TO_SEARCH", updated_fields)
        return self._search_and_rank_routes(conversation_id, query)

    def _search_and_rank_routes(self, conversation_id: str, query: TripQuery) -> str:
        """Fan-out search across providers and rank results using optimizer."""
        source = query.source or ""
        destination = query.destination or ""
        budget = query.budget or 1000.0

        all_options: list[RouteOption] = []
        for provider in self.providers:
            try:
                results = provider.search(source, destination, depart_time=query.depart_time)
                all_options.extend(results)
            except Exception:
                logger.exception("Error searching provider %s", provider.name)

        if not all_options:
            return f"Sorry, no routes could be found for {source} ➡️ {destination}."

        # Rank using Optimizer with budget concession & travel time constraints
        ranked, cutoff, is_widened = rank_route_options(
            all_options,
            budget=budget,
            custom_concession=query.concession_pct,
            max_travel_time_hours=query.max_travel_time_hours,
        )

        if not ranked:
            return (
                f"No travel options found under ₹{budget:,.0f} for {source} ➡️ {destination} "
                f"(even with a 10% budget concession up to ₹{budget * 1.10:,.0f}). "
                "Try increasing your budget or checking a different date!"
            )

        # Build user response
        concession_msg = ""
        if is_widened:
            concession_msg = (
                f"\n\n*(Note: No options were available under ₹{budget:,.0f}. "
                f"Showing top matches within a concession budget up to ₹{cutoff:,.0f})*\n"
            )

        response_lines = [
            f"📍 **Top Travel Routes for {source} ➡️ {destination}**"
            + (f" (Target Budget: ₹{budget:,.0f})" if not is_widened else "")
            + concession_msg
        ]

        for idx, item in enumerate(ranked[:3], 1):
            opt = item.option
            mock_tag = " `[DEMO FIXTURE]`" if opt.is_mock else ""
            response_lines.append(
                f"\n**{idx}. {opt.operator}** ({opt.mode.upper()}){mock_tag}\n"
                f"   • **Price**: ₹{opt.price:,.0f} | **Depart**: {opt.depart} | **Duration**: {opt.duration_minutes // 60}h {opt.duration_minutes % 60}m\n"
                f"   • **Seats Left**: {opt.seats_left} | **Highlights**: {item.reason}"
            )

        response_lines.append(
            "\n👉 Reply **'book option 1'**, **'book option 2'**, or **'book option 3'** to select a route!"
        )

        # Update session to RESULTS_SHOWN
        self.storage.save_session(conversation_id, "RESULTS_SHOWN", query.to_dict())
        return "\n".join(response_lines)

    def _handle_option_selection(
        self, conversation_id: str, fields: dict[str, Any], opt_num: int, text: str
    ) -> str:
        """Handle user route option selection."""
        # Re-fetch options dynamically to ensure zero stale DB caching
        query = TripQuery.from_dict(fields)
        all_options: list[RouteOption] = []
        for provider in self.providers:
            all_options.extend(provider.search(query.source or "", query.destination or ""))

        ranked, _, _ = rank_route_options(
            all_options,
            budget=query.budget or 10000.0,
            custom_concession=query.concession_pct,
            max_travel_time_hours=query.max_travel_time_hours,
        )

        if not ranked or opt_num > len(ranked):
            return f"Option {opt_num} is invalid. Please select from option 1 to {len(ranked)}."

        selected_ranked = ranked[opt_num - 1]
        opt = selected_ranked.option

        # Save selected option ID in session
        self.storage.save_session(
            conversation_id,
            "OPTION_SELECTED",
            fields,
            selected_provider=opt.mode,
            selected_option_id=opt.id,
        )

        # Register Watcher subscription for seat & price alerts
        self.storage.set_watch_subscription(
            conversation_id=conversation_id,
            option_id=opt.id,
            provider_name=opt.mode,
            source=opt.source,
            destination=opt.destination,
            watching=True,
            last_seats_left=opt.seats_left,
            last_price=opt.price,
        )

        return (
            f"✅ **You selected Option {opt_num}: {opt.operator}** (₹{opt.price:,.0f})\n\n"
            f"Would you like to:\n"
            f"1️⃣ Reply **'book now'** to get your pre-filled booking handoff deep link immediately.\n"
            f"2️⃣ Reply **'book in 30 minutes'** (or any custom time window) to schedule auto-booking!\n\n"
            f"*(TripCaspian is now actively watching seat availability for this route in the background)*"
        )

    def _schedule_delayed_booking(
        self, conversation_id: str, session: dict[str, Any], seconds: int
    ) -> str:
        """Schedule delayed auto-booking handoff using BookingScheduler."""
        job_id = self.scheduler.schedule_auto_booking(
            conversation_id,
            seconds,
            self.execute_booking_handoff,
            conversation_id,
        )

        # Update session with active_job_id
        self.storage.save_session(
            conversation_id,
            "BOOKING_PENDING",
            session["collected_fields"],
            selected_provider=session.get("selected_provider"),
            selected_option_id=session.get("selected_option_id"),
            active_job_id=job_id,
        )

        mins = max(1, seconds // 60)
        return (
            f"⏳ **Auto-booking handoff scheduled!**\n"
            f"TripCaspian will send your prefilled booking handoff link in **{mins} minutes** (Job ID: `{job_id}`).\n\n"
            f"If you change your mind, simply reply **'cancel my booking'** before the timer fires!"
        )

    def _handle_cancellation(self, conversation_id: str) -> str:
        """Cancel an active scheduled job and watch subscription."""
        session = self.storage.get_session(conversation_id)
        if not session:
            return "No active trip-planning session found to cancel."

        canceled = False
        active_job_id = session.get("active_job_id")
        if active_job_id:
            canceled = self.scheduler.cancel_job(active_job_id)

        self.storage.cancel_watch_subscription(conversation_id)
        self.storage.save_session(conversation_id, "COMPLETED", session["collected_fields"])

        if canceled:
            return "🛑 **Your scheduled auto-booking handoff has been canceled.** Background seat watching is also stopped."
        return "🛑 **Trip-planning session reset.** Background seat watching stopped."

    def execute_booking_handoff(self, conversation_id: str) -> str:
        """Execute booking handoff by fetching fresh options and generating deep link."""
        session = self.storage.get_session(conversation_id)
        if not session or not session.get("selected_option_id"):
            msg = "Error: Could not retrieve selected trip option."
            if self.client:
                self.client.send_message(conversation_id=conversation_id, text=msg)
            return msg

        fields = session["collected_fields"]
        opt_id = session["selected_option_id"]
        query = TripQuery.from_dict(fields)

        # Fetch fresh option details (Zero Stale DB Cache)
        all_options: list[RouteOption] = []
        for provider in self.providers:
            all_options.extend(provider.search(query.source or "", query.destination or ""))

        selected_opt = next((o for o in all_options if o.id == opt_id), None)
        if not selected_opt and all_options:
            selected_opt = all_options[0]

        if not selected_opt:
            msg = "Error: Route option is no longer available."
            if self.client:
                self.client.send_message(conversation_id=conversation_id, text=msg)
            return msg

        # Generate deep link from provider
        provider_instance = self.providers[0]
        if "bus" in selected_opt.mode:
            provider_instance = self.providers[1]
        elif "cab" in selected_opt.mode:
            provider_instance = self.providers[2]

        deep_link = provider_instance.build_booking_link(selected_opt)

        handoff_message = (
            f"🎉 **Your Trip Booking Handoff is Ready!**\n\n"
            f"• **Route**: {selected_opt.source} ➡️ {selected_opt.destination}\n"
            f"• **Operator**: {selected_opt.operator}\n"
            f"• **Current Fare**: ₹{selected_opt.price:,.0f}\n"
            f"• **Departure**: {selected_opt.depart}\n\n"
            f"🔗 **Complete Checkout**: [Click here to book now]({deep_link})\n\n"
            f"*(Per security requirements, payment & passenger details are completed on the official portal)*"
        )

        # Mark session completed
        self.storage.save_session(conversation_id, "COMPLETED", fields)

        # Push outbound message via Caspian if called asynchronously
        if self.client:
            try:
                self.client.send_message(conversation_id=conversation_id, text=handoff_message)
            except Exception:
                logger.exception("Failed to send handoff message via Caspian client.")

        return handoff_message
