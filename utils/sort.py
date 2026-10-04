import re

def extract_version(text: str) -> str | None:
    match = re.search(r'(\d+\.\d+\.\d+\.\d+)', text)
    return match.group(1) if match else None
