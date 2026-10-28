"""Installation-specific settings for the Mac photo share-sheet Shortcut."""
from .pi_shortcuts import COMMAND_COLOR, COMMAND_GLYPH


MAC_SHORTCUT_NAME = "To Mac LLM"
MAC_LAN_HOST = "m4mac.local"
MAC_TAILSCALE_HOST = "100.82.91.77"
MAC_SSH_USER = "jacobr"
MAC_INBOX_COMMAND = "/usr/bin/python3 -I /Users/jacobr/dev/scripts/macbook/scripts/llm_photo_inbox.py"
MAC_NOTIFICATION_TITLE = "Mac LLM"
MAC_COLOR = COMMAND_COLOR
MAC_GLYPH = COMMAND_GLYPH
