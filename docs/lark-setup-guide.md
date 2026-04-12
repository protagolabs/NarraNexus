# Lark / Feishu Bot Setup Guide

This guide walks you through creating a Feishu/Lark application and connecting it to your NarraNexus agent.

## Prerequisites

- A Feishu (feishu.cn) or Lark (larksuite.com) workspace account
- Admin or developer permissions in the workspace
- Node.js installed (`npm install -g @larksuite/cli`)

## Step 1: Create an Application

1. Go to the open platform:
   - Feishu (China): https://open.feishu.cn/app
   - Lark (International): https://open.larksuite.com/app

2. Click **Create Custom App**

3. Fill in:
   - **App Name**: Choose a name (e.g. "My AI Assistant")
   - **Description**: Brief description
   - **App Icon**: Upload an icon (optional)

4. Click **Create**

5. On the app page, note down:
   - **App ID** (starts with `cli_`)
   - **App Secret**

## Step 2: Enable Bot Capability

1. In your app settings, go to **Features** > **Bot**
2. Toggle **Enable Bot** to ON

## Step 3: Configure Permissions

Go to **Permissions & Scopes** and add the following scopes:

### Required Permissions

| Scope | Description | Purpose |
|-------|-------------|---------|
| `contact:user.base:readonly` | Read user basic info | Search colleagues |
| `im:message` | Send and receive messages | Messaging |
| `im:message:send_as_bot` | Send messages as bot | Bot identity |
| `im:chat` | Manage group chats | Create/search groups |
| `im:chat:readonly` | Read chat info | List conversations |
| `docx:document` | Create and edit documents | Document management |
| `docx:document:readonly` | Read documents | Fetch document content |

### Recommended Permissions (for full functionality)

| Scope | Description | Purpose |
|-------|-------------|---------|
| `calendar:calendar` | Read/write calendar | View agenda, create events |
| `calendar:calendar:readonly` | Read calendar | View schedule |
| `task:task` | Manage tasks | Create/complete tasks |
| `task:task:readonly` | Read tasks | View task list |
| `search:docs_data` | Search documents | Full-text document search |
| `im:message.p2p:readonly` | Read P2P messages | Message history |

### Event Subscription (for receiving replies)

1. Go to **Events & Callbacks**
2. Choose **WebSocket** mode (recommended, no public IP needed)
3. Add event: `im.message.receive_v1` — triggers when the bot receives a message

## Step 4: Publish the App

1. Go to **Version Management**
2. Click **Create Version**
3. Fill in version info and submit
4. For enterprise workspace: your admin will need to approve the app
5. For personal/dev workspace: it may auto-approve

## Step 5: Connect to NarraNexus

### Option A: Via Agent Config UI

1. Open NarraNexus and go to your agent's **Config** panel
2. Find the **Lark / Feishu** section
3. Enter:
   - **App ID**: Your `cli_xxx` from Step 1
   - **App Secret**: From Step 1
   - **Platform**: Select Feishu or Lark
4. Click **Bind Bot**
5. Click **Login with Feishu/Lark** and complete OAuth in the browser

### Option B: Via Chat

Tell your agent:

```
Please bind my Feishu bot. App ID is cli_xxx and App Secret is xxx. Use feishu platform.
```

The agent will use the `lark_bind_bot` tool, then prompt you to complete OAuth login.

## Step 6: Test

After binding and logging in, try asking your agent:

- "Search for colleagues in the product team"
- "Send a message to Zhang San asking about the meeting time"
- "Create a document titled 'Weekly Report'"
- "What's on my calendar today?"
- "Create a task: follow up with the design team"

## Troubleshooting

### "No Lark bot bound to this agent"
The bot isn't bound yet. Follow Step 5 above.

### "not configured" or "no user logged in"
The bot is bound but OAuth isn't complete. Click "Login with Feishu/Lark" in the Config panel.

### Permission errors
Make sure all required permissions in Step 3 are enabled and the app version is published and approved.

### Bot can't receive messages
1. Verify `im.message.receive_v1` event is subscribed (Step 3)
2. Make sure you're sending messages directly to the bot (search for the bot in Feishu and start a chat)
3. For group chats: the bot must be added to the group first

### lark-cli not found
Install it: `npm install -g @larksuite/cli`
