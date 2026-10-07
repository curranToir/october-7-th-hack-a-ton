import re


def chunk_text(text: str, max_chars: int = 2000, overlap: int = 200) -> list[str]:
    if max_chars <= 0 or not 0 <= overlap < max_chars:
        raise ValueError("require max_chars > 0 and 0 <= overlap < max_chars")
    paragraphs = re.split(r"\n[ \t]*\n+", text.replace("\r\n", "\n"))
    text = "\n\n".join(paragraph.strip() for paragraph in paragraphs if paragraph.strip())
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            window = text[start:end]
            boundary = window.rfind("\n\n")
            if boundary <= overlap:
                sentences = [match.end() for match in re.finditer(r"[.!?](?=\s)", window)]
                boundary = sentences[-1] if sentences else -1
            if boundary > overlap:
                end = start + boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = end - overlap
    return chunks
