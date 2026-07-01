#!/usr/bin/env bash
set -euo pipefail

# Emit a JSON `{"Authorization": "Bearer <token>"}` object for use as an MCP
# `headersHelper`, reading the token from 1Password at connection time so it never
# lands in .mcp.json or the environment. Claude Code re-runs this on every connect
# (and on 401/403), so a rotated key is picked up without editing any config.
#
# Usage (in an .mcp.json headersHelper field):
#   op-mcp-bearer-header.sh op://<vault>/<item>/<field>
#
# OP_ACCOUNT overrides the 1Password account (defaults to the Lambda tenant).

ref="${1:?usage: op-mcp-bearer-header.sh op://<vault>/<item>/<field>}"
account="${OP_ACCOUNT:-lambdalabs.1password.com}"

token="$(op read --account "$account" "$ref")"
jq -nc --arg t "$token" '{Authorization: "Bearer \($t)"}'
