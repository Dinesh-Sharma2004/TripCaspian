# TripCaspian — Sample Conversation Transcripts

Demonstrates the multi-channel conversational capabilities of **TripCaspian** across Telegram, Discord, and Email powered by Caspian SDK.

---

## Scenario A: Natural Multi-Turn Trip Planning & Immediate Handoff (Telegram)

**User (Telegram)**:
> Hi! I want to plan a trip from Delhi to Jaipur tomorrow morning.

**TripCaspian**:
> Understood! What is your maximum budget for the trip from Delhi to Jaipur?

**User**:
> Around ₹1,200 rupees.

**TripCaspian**:
> 📍 **Top Travel Routes for Delhi ➡️ Jaipur** (Target Budget: ₹1,200)
>
> **1. IRCTC Vande Bharat Express (20978)** (TRAIN) `[DEMO FIXTURE]`
>    • **Price**: ₹1,050 | **Depart**: 06:10 AM | **Duration**: 4h 35m
>    • **Seats Left**: 14 | **Highlights**: Best overall value within budget
>
> **2. Zingbus AC Volvo Sleeper** (BUS) `[DEMO FIXTURE]`
>    • **Price**: ₹750 | **Depart**: 07:00 AM | **Duration**: 5h 30m
>    • **Seats Left**: 8 | **Highlights**: Cheapest option
>
> **3. IntrCity SmartBus Volvo** (BUS) `[DEMO FIXTURE]`
>    • **Price**: ₹680 | **Depart**: 08:30 AM | **Duration**: 5h 30m
>    • **Seats Left**: 2 | **Highlights**: Cheapest option
>
> 👉 Reply **'book option 1'**, **'book option 2'**, or **'book option 3'** to select a route!

**User**:
> book option 1

**TripCaspian**:
> ✅ **You selected Option 1: IRCTC Vande Bharat Express (20978)** (₹1,050)
>
> Would you like to:
> 1️⃣ Reply **'book now'** to get your pre-filled booking handoff deep link immediately.
> 2️⃣ Reply **'book in 30 minutes'** (or any custom time window) to schedule auto-booking!
>
> *(TripCaspian is now actively watching seat availability for this route in the background)*

**User**:
> book now

**TripCaspian**:
> 🎉 **Your Trip Booking Handoff is Ready!**
>
> • **Route**: Delhi ➡️ Jaipur
> • **Operator**: IRCTC Vande Bharat Express (20978)
> • **Current Fare**: ₹1,050
> • **Departure**: 06:10 AM
>
> 🔗 **Complete Checkout**: [Click here to book now](https://www.irctc.co.in/nget/booking/train-list?src=NDLS&dst=JP)
>
> *(Per security requirements, payment & passenger details are completed on the official portal)*

---

## Scenario B: Budget Concession & Auto-Booking Schedule with Cancellation (Discord)

**User (Discord)**:
> Need a cab from Mumbai to Pune with a budget under ₹1,500.

**TripCaspian**:
> 📍 **Top Travel Routes for Mumbai ➡️ Pune**
>
> *(Note: No options were available under ₹1,500. Showing top matches within a concession budget up to ₹1,650)*
>
> **1. MSRTC Shivneri AC Scania** (BUS) `[DEMO FIXTURE]`
>    • **Price**: ₹510 | **Depart**: 07:30 AM | **Duration**: 3h 30m
>    • **Seats Left**: 6 | **Highlights**: Best overall value within budget
>
> 👉 Reply **'book option 1'** to select a route!

**User**:
> book option 1

**TripCaspian**:
> ✅ **You selected Option 1: MSRTC Shivneri AC Scania** (₹510)
>
> Would you like to:
> 1️⃣ Reply **'book now'** to get your pre-filled booking handoff deep link immediately.
> 2️⃣ Reply **'book in 30 minutes'** (or any custom time window) to schedule auto-booking!

**User**:
> book in 30 minutes

**TripCaspian**:
> ⏳ **Auto-booking handoff scheduled!**
> TripCaspian will send your prefilled booking handoff link in **30 minutes** (Job ID: `job_conv_discord_17000000`).
>
> If you change your mind, simply reply **'cancel my booking'** before the timer fires!

**User**:
> cancel my booking

**TripCaspian**:
> 🛑 **Your scheduled auto-booking handoff has been canceled.** Background seat watching is also stopped.

---

## Scenario C: Proactive Background Seat Running Low Alert

*(Background Watcher detects seats for option `train_irctc_12986` dropped below threshold)*

**TripCaspian (Proactive Outbound Message via `client.send_message`)**:
> 🔔 **TripCaspian Route Alert** (Delhi ➡️ Jaipur)
> ⚠️ SEAT ALERT: Only 2 seats remaining!
>
> Reply 'book option' or 'book now' to secure your booking handoff link!
