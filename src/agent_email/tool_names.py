"""Composio tool name enums for Gmail and Outlook toolkits."""

from enum import Enum


class GmailTool(str, Enum):
    """Gmail Composio tool names"""
    FETCH_EMAILS = "GMAIL_FETCH_EMAILS"
    SEND_EMAIL = "GMAIL_SEND_EMAIL"
    GET_MESSAGE = "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID"
    GET_ATTACHMENT = "GMAIL_GET_ATTACHMENT"


class OutlookTool(str, Enum):
    """Outlook Composio tool names"""
    LIST_EMAILS = "OUTLOOK_QUERY_EMAILS"
    SEND_EMAIL = "OUTLOOK_SEND_EMAIL"
    GET_MESSAGE = "OUTLOOK_GET_MESSAGE"
    DOWNLOAD_ATTACHMENT = "OUTLOOK_DOWNLOAD_OUTLOOK_ATTACHMENT"

