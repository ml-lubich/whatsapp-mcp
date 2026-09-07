import difflib
from typing import List, Dict, Any, Optional
from mcp.server.fastmcp import FastMCP
from whatsapp import (
    search_contacts as whatsapp_search_contacts,
    list_messages as whatsapp_list_messages,
    list_chats as whatsapp_list_chats,
    get_chat as whatsapp_get_chat,
    get_direct_chat_by_contact as whatsapp_get_direct_chat_by_contact,
    get_contact_chats as whatsapp_get_contact_chats,
    get_last_interaction as whatsapp_get_last_interaction,
    get_message_context as whatsapp_get_message_context,
    get_recent_messages as whatsapp_get_recent_messages,
    send_message as whatsapp_send_message,
    send_file as whatsapp_send_file,
    send_audio_message as whatsapp_audio_voice_message,
    download_media as whatsapp_download_media
)

# Initialize FastMCP server
mcp = FastMCP("whatsapp")

# Anti-repeat guard: how many trailing messages to inspect, and how close a
# match has to be (via difflib.SequenceMatcher ratio) to count as a repeat.
RECENT_CONTEXT_LIMIT = 10
DUPLICATE_RATIO_THRESHOLD = 0.92


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def _resolve_chat_jid(recipient: str) -> Optional[str]:
    """Best-effort JID for a recipient, for looking up recent messages."""
    if "@" in recipient:
        return recipient
    chat = whatsapp_get_direct_chat_by_contact(recipient)
    return chat.jid if chat else None


def _find_duplicate_outbound(message: str, recent_messages: List[Any]) -> Optional[Any]:
    """Return the recent outbound message this text duplicates, or None."""
    normalized_outgoing = _normalize_text(message)
    for msg in recent_messages:
        if not msg.is_from_me:
            continue
        normalized_existing = _normalize_text(msg.content or "")
        if normalized_existing == normalized_outgoing:
            return msg
        ratio = difflib.SequenceMatcher(None, normalized_existing, normalized_outgoing).ratio()
        if ratio >= DUPLICATE_RATIO_THRESHOLD:
            return msg
    return None


def _format_recent_context(recent_messages: List[Any]) -> List[Dict[str, Any]]:
    """Compact recent-thread view returned alongside every send_message result."""
    context = []
    for msg in recent_messages:
        text = msg.content or ""
        context.append({
            "from_me": bool(msg.is_from_me),
            "timestamp": msg.timestamp.isoformat(),
            "text": text[:200],
        })
    return context


def _thread_head(recent_messages: List[Any]) -> Optional[str]:
    """Identity of the newest message in the thread, or None for an empty thread.

    This is the token a caller must echo back via `acknowledge` to prove it has
    read the current tail of the conversation before posting. It is the message
    id, which the caller can only obtain by actually looking at the thread.
    """
    if not recent_messages:
        return None
    return max(recent_messages, key=lambda m: m.timestamp).id

@mcp.tool()
def search_contacts(query: str) -> List[Dict[str, Any]]:
    """Search WhatsApp contacts by name or phone number.
    
    Args:
        query: Search term to match against contact names or phone numbers
    """
    contacts = whatsapp_search_contacts(query)
    return contacts

@mcp.tool()
def list_messages(
    after: Optional[str] = None,
    before: Optional[str] = None,
    sender_phone_number: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_context: bool = True,
    context_before: int = 1,
    context_after: int = 1
) -> List[Dict[str, Any]]:
    """Get WhatsApp messages matching specified criteria with optional context.
    
    Args:
        after: Optional ISO-8601 formatted string to only return messages after this date
        before: Optional ISO-8601 formatted string to only return messages before this date
        sender_phone_number: Optional phone number to filter messages by sender
        chat_jid: Optional chat JID to filter messages by chat
        query: Optional search term to filter messages by content
        limit: Maximum number of messages to return (default 20)
        page: Page number for pagination (default 0)
        include_context: Whether to include messages before and after matches (default True)
        context_before: Number of messages to include before each match (default 1)
        context_after: Number of messages to include after each match (default 1)
    """
    messages = whatsapp_list_messages(
        after=after,
        before=before,
        sender_phone_number=sender_phone_number,
        chat_jid=chat_jid,
        query=query,
        limit=limit,
        page=page,
        include_context=include_context,
        context_before=context_before,
        context_after=context_after
    )
    return messages

@mcp.tool()
def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active"
) -> List[Dict[str, Any]]:
    """Get WhatsApp chats matching specified criteria.
    
    Args:
        query: Optional search term to filter chats by name or JID
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
        include_last_message: Whether to include the last message in each chat (default True)
        sort_by: Field to sort results by, either "last_active" or "name" (default "last_active")
    """
    chats = whatsapp_list_chats(
        query=query,
        limit=limit,
        page=page,
        include_last_message=include_last_message,
        sort_by=sort_by
    )
    return chats

@mcp.tool()
def get_chat(chat_jid: str, include_last_message: bool = True) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by JID.
    
    Args:
        chat_jid: The JID of the chat to retrieve
        include_last_message: Whether to include the last message (default True)
    """
    chat = whatsapp_get_chat(chat_jid, include_last_message)
    return chat

@mcp.tool()
def get_direct_chat_by_contact(sender_phone_number: str) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by sender phone number.
    
    Args:
        sender_phone_number: The phone number to search for
    """
    chat = whatsapp_get_direct_chat_by_contact(sender_phone_number)
    return chat

@mcp.tool()
def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Dict[str, Any]]:
    """Get all WhatsApp chats involving the contact.
    
    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    chats = whatsapp_get_contact_chats(jid, limit, page)
    return chats

@mcp.tool()
def get_last_interaction(jid: str) -> str:
    """Get most recent WhatsApp message involving the contact.
    
    Args:
        jid: The JID of the contact to search for
    """
    message = whatsapp_get_last_interaction(jid)
    return message

@mcp.tool()
def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5
) -> Dict[str, Any]:
    """Get context around a specific WhatsApp message.
    
    Args:
        message_id: The ID of the message to get context for
        before: Number of messages to include before the target message (default 5)
        after: Number of messages to include after the target message (default 5)
    """
    context = whatsapp_get_message_context(message_id, before, after)
    return context

@mcp.tool()
def send_message(
    recipient: str,
    message: str,
    acknowledge: Optional[str] = None,
    force: bool = False
) -> Dict[str, Any]:
    """Send a WhatsApp message to a person or group. For group chats use the JID.

    REVIEW BEFORE YOU SEND. Multiple agents share these chats, so a send is a
    coordination point, not a fire-and-forget. On your first attempt, omit
    `acknowledge`: the send is held and you get back `recent_context` (the last
    messages in the thread) plus `thread_head`. Read that context — has someone
    already said this? did the other party already reply? is this even still the
    right thing to post? — then call again with `acknowledge=<thread_head>` to
    confirm you've seen the current tail. Near-duplicate outbound messages are
    still blocked. Both gates are bypassed with force=true (use sparingly).

    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        message: The message text to send
        acknowledge: The `thread_head` token from the recent context you just reviewed.
                 Required to send into a non-empty thread; proves you read the latest messages.
        force: If True, send even if the review gate or duplicate guard would block (default False)

    Returns:
        A dictionary with success status, a status message, recent_context
        (the last messages in the thread), and thread_head (the token to acknowledge).
    """
    # Validate input
    if not recipient:
        return {
            "success": False,
            "message": "Recipient must be provided",
            "recent_context": [],
            "thread_head": None
        }

    recent_messages: List[Any] = []
    try:
        chat_jid = _resolve_chat_jid(recipient)
        if chat_jid:
            recent_messages = whatsapp_get_recent_messages(chat_jid, limit=RECENT_CONTEXT_LIMIT)
    except Exception:
        # Context is a nice-to-have; a fetch failure must never block a real send.
        recent_messages = []

    recent_context = _format_recent_context(recent_messages)
    thread_head = _thread_head(recent_messages)

    if not force:
        # Coordination gate: refuse to post into a live thread the caller hasn't
        # confirmed it read. An empty thread (thread_head is None) has nothing to
        # review, so first contact goes straight through.
        if thread_head is not None and acknowledge != thread_head:
            return {
                "success": False,
                "message": (
                    "Not sent: review the thread first. Read the messages in recent_context "
                    "below, make sure this message still makes sense and isn't already covered, "
                    f"then call send_message again with acknowledge=\"{thread_head}\". "
                    "Pass force=true only to skip this review."
                ),
                "recent_context": recent_context,
                "thread_head": thread_head
            }

        duplicate = _find_duplicate_outbound(message, recent_messages)
        if duplicate is not None:
            return {
                "success": False,
                "message": (
                    "Not sent: this message looks identical to one we already sent at "
                    f"{duplicate.timestamp.isoformat()}. Pass force=true to send it anyway."
                ),
                "recent_context": recent_context,
                "thread_head": thread_head
            }

    # Call the whatsapp_send_message function with the unified recipient parameter
    success, status_message = whatsapp_send_message(recipient, message)
    return {
        "success": success,
        "message": status_message,
        "recent_context": recent_context,
        "thread_head": thread_head
    }

@mcp.tool()
def send_file(recipient: str, media_path: str) -> Dict[str, Any]:
    """Send a file such as a picture, raw audio, video or document via WhatsApp to the specified recipient. For group messages use the JID.
    
    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the media file to send (image, video, document)
    
    Returns:
        A dictionary containing success status and a status message
    """
    
    # Call the whatsapp_send_file function
    success, status_message = whatsapp_send_file(recipient, media_path)
    return {
        "success": success,
        "message": status_message
    }

@mcp.tool()
def send_audio_message(recipient: str, media_path: str) -> Dict[str, Any]:
    """Send any audio file as a WhatsApp audio message to the specified recipient. For group messages use the JID. If it errors due to ffmpeg not being installed, use send_file instead.
    
    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the audio file to send (will be converted to Opus .ogg if it's not a .ogg file)
    
    Returns:
        A dictionary containing success status and a status message
    """
    success, status_message = whatsapp_audio_voice_message(recipient, media_path)
    return {
        "success": success,
        "message": status_message
    }

@mcp.tool()
def download_media(message_id: str, chat_jid: str) -> Dict[str, Any]:
    """Download media from a WhatsApp message and get the local file path.
    
    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
    
    Returns:
        A dictionary containing success status, a status message, and the file path if successful
    """
    file_path = whatsapp_download_media(message_id, chat_jid)
    
    if file_path:
        return {
            "success": True,
            "message": "Media downloaded successfully",
            "file_path": file_path
        }
    else:
        return {
            "success": False,
            "message": "Failed to download media"
        }

def run() -> None:
    """Console entry point for `wa-mcp` (stdio MCP server)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()