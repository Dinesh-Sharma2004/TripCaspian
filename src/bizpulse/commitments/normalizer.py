"""BizPulse Message Normalizer Module.

Cleans signatures, quoted replies, HTML tags, tracking URLs, and collapses whitespace.
"""

import re

SIGNATURE_MARKERS = [
    r'^\s*--\s*$',
    r'^\s*regards,?\s*$',
    r'^\s*thanks,?\s*$',
    r'^\s*sincerely,?\s*$',
    r'^\s*best regards,?\s*$',
    r'^\s*warm regards,?\s*$',
    r'^\s*thank you,?\s*$',
]

QUOTED_MARKERS = [
    r'^\s*—+\s*previous messages?\s*—+\s*$',
    r'^\s*original message\s*$',
    r'^\s*from:.*',
    r'^\s*sent:.*',
    r'^\s*on\s+.*wrote:\s*$',
]


def normalize_message(text: str, subject: str | None = None) -> str:
    """Normalize input message to extract the core conversational message.

    Strips signatures, quoted emails, html tags, collapses whitespace,
    and hard caps at 2000 characters.
    """
    if not text:
        return ""

    # 1. Strip HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)

    # 2. Strip tracking URLs (basic replacement of urls with a placeholder or space)
    text = re.sub(r'https?://[^\s>]+', '[URL]', text)

    # 3. Process lines to remove quotes and signatures
    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        stripped_line = line.strip()
        
        # Check for lines starting with '>' (quoted email line)
        if stripped_line.startswith('>'):
            continue
            
        # Check for email headers or quoted divider markers
        if any(re.match(marker, stripped_line, re.IGNORECASE) for marker in QUOTED_MARKERS):
            break
            
        # Check for signature markers
        if any(re.match(marker, stripped_line, re.IGNORECASE) for marker in SIGNATURE_MARKERS):
            break
            
        cleaned_lines.append(line)

    # 4. Collapse whitespace
    normalized_text = " ".join(" ".join(cleaned_lines).split())

    # 5. Prepend subject context if it's an email with subject
    if subject:
        subject_clean = subject.strip()
        if subject_clean:
            normalized_text = f"Subject: {subject_clean} | Message: {normalized_text}"

    # 6. Hard cap at 2000 characters
    return normalized_text[:2000]
