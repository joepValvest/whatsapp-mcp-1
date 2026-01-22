"""
Supabase client for WhatsApp MCP server.
Handles all database operations for messages, conversations, and contacts.
"""
import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Load .env file from the same directory
load_dotenv(Path(__file__).parent / '.env')
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from supabase import create_client, Client

# Environment variables
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')  # Use service role key for server-side operations

# Initialize Supabase client
_supabase_client: Optional[Client] = None

def get_supabase() -> Client:
    """Get or create Supabase client."""
    global _supabase_client
    if _supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY environment variables are required")
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client


@dataclass
class Message:
    timestamp: datetime
    sender: str
    content: str
    is_from_me: bool
    chat_jid: str
    id: str
    chat_name: Optional[str] = None
    media_type: Optional[str] = None


@dataclass
class Chat:
    jid: str
    name: Optional[str]
    last_message_time: Optional[datetime]
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    last_is_from_me: Optional[bool] = None

    @property
    def is_group(self) -> bool:
        """Determine if chat is a group based on JID pattern."""
        return self.jid.endswith("@g.us")


@dataclass
class Contact:
    phone_number: str
    name: Optional[str]
    jid: str


@dataclass
class MessageContext:
    message: Message
    before: List[Message]
    after: List[Message]


def get_sender_name(sender_jid: str) -> str:
    """Get contact name from sender JID."""
    try:
        supabase = get_supabase()

        # First try matching by exact JID (contact_identifier)
        result = supabase.table('conversations') \
            .select('contact_name') \
            .eq('contact_identifier', sender_jid) \
            .eq('channel', 'whatsapp') \
            .limit(1) \
            .execute()

        if result.data and result.data[0].get('contact_name'):
            return result.data[0]['contact_name']

        # If no result, try looking for the number within JIDs
        if '@' in sender_jid:
            phone_part = sender_jid.split('@')[0]
        else:
            phone_part = sender_jid

        result = supabase.table('conversations') \
            .select('contact_name') \
            .ilike('contact_identifier', f'%{phone_part}%') \
            .eq('channel', 'whatsapp') \
            .limit(1) \
            .execute()

        if result.data and result.data[0].get('contact_name'):
            return result.data[0]['contact_name']

        return sender_jid

    except Exception as e:
        print(f"Database error while getting sender name: {e}")
        return sender_jid


def format_message(message: Message, show_chat_info: bool = True) -> str:
    """Format a single message with consistent formatting."""
    output = ""

    if show_chat_info and message.chat_name:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] Chat: {message.chat_name} "
    else:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] "

    content_prefix = ""
    if message.media_type:
        content_prefix = f"[{message.media_type} - Message ID: {message.id} - Chat JID: {message.chat_jid}] "

    try:
        sender_name = get_sender_name(message.sender) if not message.is_from_me else "Me"
        output += f"From: {sender_name}: {content_prefix}{message.content}\n"
    except Exception as e:
        print(f"Error formatting message: {e}")
    return output


def format_messages_list(messages: List[Message], show_chat_info: bool = True) -> str:
    """Format a list of messages."""
    if not messages:
        return "No messages to display."

    output = ""
    for message in messages:
        output += format_message(message, show_chat_info)
    return output


def _row_to_message(row: Dict[str, Any], conversation_name: Optional[str] = None) -> Message:
    """Convert a database row to a Message object."""
    # Parse timestamp
    timestamp_str = row.get('created_at')
    if isinstance(timestamp_str, str):
        # Handle ISO format with or without timezone
        timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
    else:
        timestamp = timestamp_str

    # Determine if message is from me based on direction
    is_from_me = row.get('direction') == 'outbound'

    # Get media type from metadata or payload
    media_type = None
    metadata = row.get('metadata') or {}
    payload = row.get('payload') or {}
    if isinstance(metadata, dict):
        media_type = metadata.get('media_type')
    if not media_type and isinstance(payload, dict):
        media_type = payload.get('media_type')

    # Get conversation info
    conversation = row.get('conversations', {}) or {}
    chat_jid = row.get('recipient') if is_from_me else row.get('sender')
    if not chat_jid:
        chat_jid = conversation.get('contact_identifier', '')

    return Message(
        timestamp=timestamp,
        sender=row.get('sender', ''),
        content=row.get('body', ''),
        is_from_me=is_from_me,
        chat_jid=chat_jid,
        id=str(row.get('id', '')),
        chat_name=conversation_name or conversation.get('contact_name'),
        media_type=media_type
    )


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
) -> str:
    """Get messages matching the specified criteria with optional context."""
    try:
        supabase = get_supabase()

        # Build query
        q = supabase.table('messages') \
            .select('*, conversations!inner(contact_identifier, contact_name)') \
            .eq('channel', 'whatsapp')

        # Add filters
        if after:
            after_dt = datetime.fromisoformat(after)
            q = q.gte('created_at', after_dt.isoformat())

        if before:
            before_dt = datetime.fromisoformat(before)
            q = q.lte('created_at', before_dt.isoformat())

        if sender_phone_number:
            q = q.eq('sender', sender_phone_number)

        if chat_jid:
            q = q.eq('conversations.contact_identifier', chat_jid)

        if query:
            q = q.ilike('body', f'%{query}%')

        # Add pagination and ordering
        offset = page * limit
        q = q.order('created_at', desc=True).range(offset, offset + limit - 1)

        result = q.execute()

        messages = []
        for row in result.data:
            messages.append(_row_to_message(row))

        if include_context and messages:
            messages_with_context = []
            for msg in messages:
                context = get_message_context(msg.id, context_before, context_after)
                messages_with_context.extend(context.before)
                messages_with_context.append(context.message)
                messages_with_context.extend(context.after)
            return format_messages_list(messages_with_context, show_chat_info=True)

        return format_messages_list(messages, show_chat_info=True)

    except Exception as e:
        print(f"Database error: {e}")
        return f"Error: {e}"


def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5
) -> MessageContext:
    """Get context around a specific message."""
    try:
        supabase = get_supabase()

        # Get the target message
        result = supabase.table('messages') \
            .select('*, conversations!inner(contact_identifier, contact_name)') \
            .eq('id', message_id) \
            .single() \
            .execute()

        if not result.data:
            raise ValueError(f"Message with ID {message_id} not found")

        target_message = _row_to_message(result.data)
        conversation_id = result.data.get('conversation_id')
        target_timestamp = result.data.get('created_at')

        # Get messages before
        before_result = supabase.table('messages') \
            .select('*, conversations!inner(contact_identifier, contact_name)') \
            .eq('conversation_id', conversation_id) \
            .lt('created_at', target_timestamp) \
            .order('created_at', desc=True) \
            .limit(before) \
            .execute()

        before_messages = [_row_to_message(row) for row in reversed(before_result.data)]

        # Get messages after
        after_result = supabase.table('messages') \
            .select('*, conversations!inner(contact_identifier, contact_name)') \
            .eq('conversation_id', conversation_id) \
            .gt('created_at', target_timestamp) \
            .order('created_at', desc=False) \
            .limit(after) \
            .execute()

        after_messages = [_row_to_message(row) for row in after_result.data]

        return MessageContext(
            message=target_message,
            before=before_messages,
            after=after_messages
        )

    except Exception as e:
        print(f"Database error: {e}")
        raise


def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active"
) -> List[Dict[str, Any]]:
    """Get chats matching the specified criteria."""
    try:
        supabase = get_supabase()

        # Build query
        q = supabase.table('conversations') \
            .select('*') \
            .eq('channel', 'whatsapp')

        if query:
            q = q.or_(f'contact_name.ilike.%{query}%,contact_identifier.ilike.%{query}%')

        # Add sorting
        if sort_by == "last_active":
            q = q.order('last_message_at', desc=True, nullsfirst=False)
        else:
            q = q.order('contact_name', desc=False, nullsfirst=False)

        # Add pagination
        offset = page * limit
        q = q.range(offset, offset + limit - 1)

        result = q.execute()

        chats = []
        for row in result.data:
            chat = Chat(
                jid=row.get('contact_identifier', ''),
                name=row.get('contact_name'),
                last_message_time=datetime.fromisoformat(row['last_message_at'].replace('Z', '+00:00')) if row.get('last_message_at') else None,
            )

            # Get last message if requested
            if include_last_message and row.get('id'):
                last_msg = supabase.table('messages') \
                    .select('body, sender, direction') \
                    .eq('conversation_id', row['id']) \
                    .order('created_at', desc=True) \
                    .limit(1) \
                    .execute()

                if last_msg.data:
                    chat.last_message = last_msg.data[0].get('body')
                    chat.last_sender = last_msg.data[0].get('sender')
                    chat.last_is_from_me = last_msg.data[0].get('direction') == 'outbound'

            chats.append(asdict(chat))

        return chats

    except Exception as e:
        print(f"Database error: {e}")
        return []


def search_contacts(query: str) -> List[Dict[str, Any]]:
    """Search contacts by name or phone number."""
    try:
        supabase = get_supabase()

        result = supabase.table('conversations') \
            .select('contact_identifier, contact_name') \
            .eq('channel', 'whatsapp') \
            .not_.ilike('contact_identifier', '%@g.us') \
            .or_(f'contact_name.ilike.%{query}%,contact_identifier.ilike.%{query}%') \
            .order('contact_name', nullsfirst=False) \
            .limit(50) \
            .execute()

        contacts = []
        for row in result.data:
            jid = row.get('contact_identifier', '')
            contacts.append({
                'phone_number': jid.split('@')[0] if '@' in jid else jid,
                'name': row.get('contact_name'),
                'jid': jid
            })

        return contacts

    except Exception as e:
        print(f"Database error: {e}")
        return []


def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Dict[str, Any]]:
    """Get all chats involving the contact."""
    try:
        supabase = get_supabase()

        result = supabase.table('conversations') \
            .select('*') \
            .eq('channel', 'whatsapp') \
            .or_(f'contact_identifier.eq.{jid}') \
            .order('last_message_at', desc=True) \
            .range(page * limit, (page + 1) * limit - 1) \
            .execute()

        chats = []
        for row in result.data:
            chat = {
                'jid': row.get('contact_identifier', ''),
                'name': row.get('contact_name'),
                'last_message_time': row.get('last_message_at'),
            }
            chats.append(chat)

        return chats

    except Exception as e:
        print(f"Database error: {e}")
        return []


def get_last_interaction(jid: str) -> Optional[str]:
    """Get most recent message involving the contact."""
    try:
        supabase = get_supabase()

        # Get conversation
        conv_result = supabase.table('conversations') \
            .select('id, contact_name') \
            .eq('contact_identifier', jid) \
            .eq('channel', 'whatsapp') \
            .limit(1) \
            .execute()

        if not conv_result.data:
            return None

        conversation_id = conv_result.data[0]['id']
        contact_name = conv_result.data[0].get('contact_name')

        # Get last message
        msg_result = supabase.table('messages') \
            .select('*') \
            .eq('conversation_id', conversation_id) \
            .order('created_at', desc=True) \
            .limit(1) \
            .execute()

        if not msg_result.data:
            return None

        message = _row_to_message(msg_result.data[0], contact_name)
        return format_message(message)

    except Exception as e:
        print(f"Database error: {e}")
        return None


def get_chat(chat_jid: str, include_last_message: bool = True) -> Optional[Dict[str, Any]]:
    """Get chat metadata by JID."""
    try:
        supabase = get_supabase()

        result = supabase.table('conversations') \
            .select('*') \
            .eq('contact_identifier', chat_jid) \
            .eq('channel', 'whatsapp') \
            .limit(1) \
            .execute()

        if not result.data:
            return None

        row = result.data[0]
        chat = {
            'jid': row.get('contact_identifier', ''),
            'name': row.get('contact_name'),
            'last_message_time': row.get('last_message_at'),
        }

        if include_last_message and row.get('id'):
            last_msg = supabase.table('messages') \
                .select('body, sender, direction') \
                .eq('conversation_id', row['id']) \
                .order('created_at', desc=True) \
                .limit(1) \
                .execute()

            if last_msg.data:
                chat['last_message'] = last_msg.data[0].get('body')
                chat['last_sender'] = last_msg.data[0].get('sender')
                chat['last_is_from_me'] = last_msg.data[0].get('direction') == 'outbound'

        return chat

    except Exception as e:
        print(f"Database error: {e}")
        return None


def get_direct_chat_by_contact(sender_phone_number: str) -> Optional[Dict[str, Any]]:
    """Get chat metadata by sender phone number."""
    try:
        supabase = get_supabase()

        result = supabase.table('conversations') \
            .select('*') \
            .eq('channel', 'whatsapp') \
            .ilike('contact_identifier', f'%{sender_phone_number}%') \
            .not_.ilike('contact_identifier', '%@g.us') \
            .limit(1) \
            .execute()

        if not result.data:
            return None

        row = result.data[0]
        chat = {
            'jid': row.get('contact_identifier', ''),
            'name': row.get('contact_name'),
            'last_message_time': row.get('last_message_at'),
        }

        # Get last message
        if row.get('id'):
            last_msg = supabase.table('messages') \
                .select('body, sender, direction') \
                .eq('conversation_id', row['id']) \
                .order('created_at', desc=True) \
                .limit(1) \
                .execute()

            if last_msg.data:
                chat['last_message'] = last_msg.data[0].get('body')
                chat['last_sender'] = last_msg.data[0].get('sender')
                chat['last_is_from_me'] = last_msg.data[0].get('direction') == 'outbound'

        return chat

    except Exception as e:
        print(f"Database error: {e}")
        return None


# Message saving functions (for Go bridge to call, or for syncing)

def save_message(
    conversation_jid: str,
    sender: str,
    recipient: str,
    body: str,
    direction: str,  # 'inbound' or 'outbound'
    external_id: Optional[str] = None,
    media_type: Optional[str] = None,
    timestamp: Optional[datetime] = None
) -> Optional[str]:
    """Save a new message to Supabase."""
    try:
        supabase = get_supabase()

        # Get or create conversation
        conv_result = supabase.table('conversations') \
            .select('id') \
            .eq('contact_identifier', conversation_jid) \
            .eq('channel', 'whatsapp') \
            .limit(1) \
            .execute()

        if conv_result.data:
            conversation_id = conv_result.data[0]['id']
        else:
            # Create new conversation
            new_conv = supabase.table('conversations').insert({
                'channel': 'whatsapp',
                'contact_identifier': conversation_jid,
                'contact_name': None,  # Will be updated later if available
                'status': 'active'
            }).execute()
            conversation_id = new_conv.data[0]['id']

        # Insert message
        now = timestamp or datetime.utcnow()
        message_data = {
            'conversation_id': conversation_id,
            'channel': 'whatsapp',
            'direction': direction,
            'sender': sender,
            'recipient': recipient,
            'body': body,
            'created_at': now.isoformat(),
            'updated_at': now.isoformat(),
            'topic': 'chat',
            'extension': 'text',
            'external_id': external_id,
            'metadata': {'media_type': media_type} if media_type else None
        }

        result = supabase.table('messages').insert(message_data).execute()

        # Update conversation last_message_at
        supabase.table('conversations') \
            .update({'last_message_at': now.isoformat()}) \
            .eq('id', conversation_id) \
            .execute()

        return result.data[0]['id'] if result.data else None

    except Exception as e:
        print(f"Error saving message: {e}")
        return None


def update_contact_name(jid: str, name: str) -> bool:
    """Update the contact name for a conversation."""
    try:
        supabase = get_supabase()

        supabase.table('conversations') \
            .update({'contact_name': name}) \
            .eq('contact_identifier', jid) \
            .eq('channel', 'whatsapp') \
            .execute()

        return True

    except Exception as e:
        print(f"Error updating contact name: {e}")
        return False
