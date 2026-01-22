package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"
)

// SupabaseClient handles communication with Supabase REST API
type SupabaseClient struct {
	URL    string
	Key    string
	client *http.Client
}

// NewSupabaseClient creates a new Supabase client from environment variables
func NewSupabaseClient() (*SupabaseClient, error) {
	url := os.Getenv("SUPABASE_URL")
	key := os.Getenv("SUPABASE_KEY")

	if url == "" || key == "" {
		return nil, fmt.Errorf("SUPABASE_URL and SUPABASE_KEY environment variables are required")
	}

	return &SupabaseClient{
		URL:    url,
		Key:    key,
		client: &http.Client{Timeout: 30 * time.Second},
	}, nil
}

// makeRequest makes an authenticated request to Supabase
func (s *SupabaseClient) makeRequest(method, endpoint string, body interface{}) ([]byte, error) {
	var reqBody io.Reader
	if body != nil {
		jsonBody, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("failed to marshal body: %v", err)
		}
		reqBody = bytes.NewBuffer(jsonBody)
	}

	url := fmt.Sprintf("%s/rest/v1/%s", s.URL, endpoint)
	req, err := http.NewRequest(method, url, reqBody)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %v", err)
	}

	req.Header.Set("apikey", s.Key)
	req.Header.Set("Authorization", fmt.Sprintf("Bearer %s", s.Key))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Prefer", "return=representation")

	resp, err := s.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request failed: %v", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %v", err)
	}

	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("API error (status %d): %s", resp.StatusCode, string(respBody))
	}

	return respBody, nil
}

// Conversation represents a Supabase conversation record
type Conversation struct {
	ID                string     `json:"id,omitempty"`
	Channel           string     `json:"channel"`
	ContactIdentifier string     `json:"contact_identifier"`
	ContactName       *string    `json:"contact_name,omitempty"`
	LastMessageAt     *time.Time `json:"last_message_at,omitempty"`
	Status            string     `json:"status"`
	UnreadCount       int        `json:"unread_count,omitempty"`
}

// SupabaseMessage represents a Supabase message record
type SupabaseMessage struct {
	ID             string                 `json:"id,omitempty"`
	ConversationID string                 `json:"conversation_id"`
	Channel        string                 `json:"channel"`
	Direction      string                 `json:"direction"`
	Sender         string                 `json:"sender"`
	Recipient      string                 `json:"recipient"`
	Body           *string                `json:"body,omitempty"`
	ExternalID     *string                `json:"external_id,omitempty"`
	Metadata       map[string]interface{} `json:"metadata,omitempty"`
	IsRead         bool                   `json:"is_read,omitempty"`
	Status         *string                `json:"status,omitempty"`
}

// GetOrCreateConversation gets an existing conversation or creates a new one
func (s *SupabaseClient) GetOrCreateConversation(jid, name string) (string, error) {
	// First, try to find existing conversation
	endpoint := fmt.Sprintf("conversations?contact_identifier=eq.%s&channel=eq.whatsapp&select=id", jid)
	resp, err := s.makeRequest("GET", endpoint, nil)
	if err != nil {
		return "", fmt.Errorf("failed to query conversation: %v", err)
	}

	var conversations []struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(resp, &conversations); err != nil {
		return "", fmt.Errorf("failed to parse conversation response: %v", err)
	}

	if len(conversations) > 0 {
		return conversations[0].ID, nil
	}

	// Create new conversation
	conv := Conversation{
		Channel:           "whatsapp",
		ContactIdentifier: jid,
		Status:            "active",
	}
	if name != "" {
		conv.ContactName = &name
	}

	resp, err = s.makeRequest("POST", "conversations", conv)
	if err != nil {
		return "", fmt.Errorf("failed to create conversation: %v", err)
	}

	var newConversations []struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(resp, &newConversations); err != nil {
		return "", fmt.Errorf("failed to parse new conversation response: %v", err)
	}

	if len(newConversations) == 0 {
		return "", fmt.Errorf("no conversation returned after creation")
	}

	return newConversations[0].ID, nil
}

// UpdateConversationLastMessage updates the last_message_at timestamp for a conversation
func (s *SupabaseClient) UpdateConversationLastMessage(conversationID string, timestamp time.Time) error {
	update := map[string]interface{}{
		"last_message_at": timestamp.Format(time.RFC3339),
	}

	endpoint := fmt.Sprintf("conversations?id=eq.%s", conversationID)
	_, err := s.makeRequest("PATCH", endpoint, update)
	return err
}

// UpdateConversationName updates the contact_name for a conversation
func (s *SupabaseClient) UpdateConversationName(jid, name string) error {
	update := map[string]interface{}{
		"contact_name": name,
	}

	endpoint := fmt.Sprintf("conversations?contact_identifier=eq.%s&channel=eq.whatsapp", jid)
	_, err := s.makeRequest("PATCH", endpoint, update)
	return err
}

// StoreMessage stores a message in Supabase
func (s *SupabaseClient) StoreMessage(conversationID, externalID, sender, recipient, content string,
	timestamp time.Time, isFromMe bool, mediaType string) error {

	// Skip empty messages
	if content == "" && mediaType == "" {
		return nil
	}

	direction := "inbound"
	if isFromMe {
		direction = "outbound"
	}

	msg := SupabaseMessage{
		ConversationID: conversationID,
		Channel:        "whatsapp",
		Direction:      direction,
		Sender:         sender,
		Recipient:      recipient,
	}

	if content != "" {
		msg.Body = &content
	}

	if externalID != "" {
		msg.ExternalID = &externalID
	}

	if mediaType != "" {
		msg.Metadata = map[string]interface{}{
			"media_type": mediaType,
		}
	}

	_, err := s.makeRequest("POST", "messages", msg)
	if err != nil {
		return fmt.Errorf("failed to store message: %v", err)
	}

	// Update conversation last_message_at
	_ = s.UpdateConversationLastMessage(conversationID, timestamp)

	return nil
}

// SupabaseMessageStore implements the message storage interface using Supabase
type SupabaseMessageStore struct {
	client *SupabaseClient
	// Keep a cache of conversation IDs to avoid repeated lookups
	conversationCache map[string]string
}

// NewSupabaseMessageStore creates a new Supabase-backed message store
func NewSupabaseMessageStore() (*SupabaseMessageStore, error) {
	client, err := NewSupabaseClient()
	if err != nil {
		return nil, err
	}

	return &SupabaseMessageStore{
		client:            client,
		conversationCache: make(map[string]string),
	}, nil
}

// Close cleans up resources (no-op for Supabase)
func (s *SupabaseMessageStore) Close() error {
	return nil
}

// StoreChat stores or updates a chat/conversation in Supabase
func (s *SupabaseMessageStore) StoreChat(jid, name string, lastMessageTime time.Time) error {
	conversationID, err := s.client.GetOrCreateConversation(jid, name)
	if err != nil {
		return err
	}

	// Cache the conversation ID
	s.conversationCache[jid] = conversationID

	// Update the name if provided
	if name != "" {
		_ = s.client.UpdateConversationName(jid, name)
	}

	// Update last message time
	return s.client.UpdateConversationLastMessage(conversationID, lastMessageTime)
}

// StoreMessage stores a message in Supabase
func (s *SupabaseMessageStore) StoreMessage(id, chatJID, sender, content string, timestamp time.Time, isFromMe bool,
	mediaType, filename, url string, mediaKey, fileSHA256, fileEncSHA256 []byte, fileLength uint64) error {

	// Get or create conversation
	conversationID, ok := s.conversationCache[chatJID]
	if !ok {
		var err error
		conversationID, err = s.client.GetOrCreateConversation(chatJID, "")
		if err != nil {
			return fmt.Errorf("failed to get conversation: %v", err)
		}
		s.conversationCache[chatJID] = conversationID
	}

	// Determine recipient (the other party in the conversation)
	recipient := chatJID
	if isFromMe {
		recipient = chatJID
	}

	return s.client.StoreMessage(conversationID, id, sender, recipient, content, timestamp, isFromMe, mediaType)
}

// GetMessages retrieves messages from a chat (minimal implementation for compatibility)
func (s *SupabaseMessageStore) GetMessages(chatJID string, limit int) ([]Message, error) {
	// This would require additional API calls - for now, return empty
	// The Python MCP server handles message retrieval
	return []Message{}, nil
}

// GetChats retrieves all chats (minimal implementation for compatibility)
func (s *SupabaseMessageStore) GetChats() (map[string]time.Time, error) {
	// This would require additional API calls - for now, return empty
	// The Python MCP server handles chat retrieval
	return make(map[string]time.Time), nil
}

// GetMediaInfo retrieves media info for a message (not stored in Supabase yet)
func (s *SupabaseMessageStore) GetMediaInfo(id, chatJID string) (string, string, string, []byte, []byte, []byte, uint64, error) {
	// Media info is stored in metadata - would need to query Supabase
	// For now, return empty (media download will need the local SQLite fallback)
	return "", "", "", nil, nil, nil, 0, fmt.Errorf("media info not available in Supabase")
}
