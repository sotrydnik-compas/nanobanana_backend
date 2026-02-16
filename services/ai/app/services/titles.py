def make_chat_title(prompt: str) -> str:
    s = (prompt or "").strip()
    if not s:
        return ""
    words = s.split()
    title = " ".join(words[:3]).strip()
    if len(title) > 64:
        title = title[:64]
    return title
