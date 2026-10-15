#!/usr/bin/env node

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  ErrorCode,
  McpError,
} from '@modelcontextprotocol/sdk/types.js';

import { platform } from 'os';
import fs from 'fs';
import { fileURLToPath } from 'url';

import { LineAutomation } from './automation/line-automation.js';
import { createLineExtensions } from './extensions/line-extensions.mjs';
import { LineToolError } from './extensions/line-runtime.mjs';

// 取得當前模組的檔案路徑
const __filename = fileURLToPath(import.meta.url);
const packageVersion = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version;

const WINDOWS_LEGACY_CHAT_REQUIREMENTS = 'The target chat must already be open. Requires LINE_MCP_CUA_DRIVER, LINE_MCP_PYTHON and LINE_MCP_SQLITE3MC_DLL so the bridge can resolve one unique local chat identity and verify the fresh active header before UI or clipboard activity.';
const MACOS_LEGACY_CHAT_UNAVAILABLE = 'Unavailable on macOS: this release returns LINE_CHAT_VERIFICATION_UNAVAILABLE before UI or clipboard activity because it has no verified exact active-chat identity backend.';

function legacyChatToolDescription(runtimePlatform, windowsDescription) {
  if (runtimePlatform === 'win32') return `${windowsDescription} ${WINDOWS_LEGACY_CHAT_REQUIREMENTS}`;
  if (runtimePlatform === 'darwin') return MACOS_LEGACY_CHAT_UNAVAILABLE;
  return 'Unavailable on this platform because the release has no verified exact active-chat identity backend.';
}

export class LineDesktopMCPServer {
  constructor({ automation, ui, extensionsEnabled = process.env.LINE_MCP_EXTENSIONS === '1', runtimePlatform = platform() } = {}) {
    const useExtensions = runtimePlatform === 'win32' && extensionsEnabled;
    this.runtimePlatform = runtimePlatform;
    this.server = new Server(
      {
        name: 'line-desktop-mcp',
        version: packageVersion,
      },
      {
        capabilities: {
          tools: {},
        },
      }
    );

    this.lineAutomation = automation ?? new LineAutomation();
    this.lineExtensions = useExtensions ? createLineExtensions(this.lineAutomation, { ui }) : undefined;
    this.setupToolHandlers();
  }

  setupToolHandlers() {
    // List available tools
    /* 圖片功能，先實作在 LineDesktopMCP_DQ 的 PC 版中
              outputSchema: {
                type: 'object',
                properties: {
                  content: {
                    type: 'array',
                    items: {
                      oneOf: [
                        // TextContent 格式
                        { type: 'text', text: 'string' , description: 'JSON string containing chat metadata and text history'},
                        // ImageContent 格式  
                        { type: 'image', data: 'string', mimeType: 'string' , description: 'last image data which Base64 encoded PNG or JPEG format in chat history'}
                      ]
                    }
                  }
                }
              }
    */
    this.server.setRequestHandler(ListToolsRequestSchema, async () => {
      if (this.lineExtensions) return { tools: this.lineExtensions.tools };
      return {
        tools: [
          {
            name: 'get_line_chatroom_history_default',
            description: legacyChatToolDescription(this.runtimePlatform, 'Read bounded loaded history from the exact named LINE group or direct chat on Windows. Use this default paging size when the needed amount is uncertain.'),
            inputSchema: {
              type: 'object',
              properties: {
                chatName: {
                  type: 'string',
                  description: 'Name of the chat/group to extract history from',
                },
                date: {
                  type: 'string',
                  description: 'Date to extract history for (YYYY-MM-DD format, defaults to today)',
                },
                messageLimit: {
                  type: 'number',
                  description: 'Maximum number of messages to extract (default: 100)',
                  default: 100,
                },
              },
              required: ['chatName'],
            },
          },
          {
            name: 'get_line_chatroom_history_long',
            description: legacyChatToolDescription(this.runtimePlatform, 'Read a larger bounded window of loaded history from the exact named LINE group or direct chat on Windows.'),
            inputSchema: {
              type: 'object',
              properties: {
                chatName: {
                  type: 'string',
                  description: 'Name of the chat/group to extract history from',
                },
                date: {
                  type: 'string',
                  description: 'Date to extract history for (YYYY-MM-DD format, defaults to today)',
                },
                messageLimit: {
                  type: 'number',
                  description: 'Maximum number of messages to extract (default: 100)',
                  default: 100,
                },
              },
              required: ['chatName'],
            },
          },
          {
            name: 'get_line_chatroom_history_short',
            description: legacyChatToolDescription(this.runtimePlatform, 'Read a smaller bounded window of recent loaded history from the exact named LINE group or direct chat on Windows.'),
            inputSchema: {
              type: 'object',
              properties: {
                chatName: {
                  type: 'string',
                  description: 'Name of the chat/group to extract history from',
                },
                date: {
                  type: 'string',
                  description: 'Date to extract history for (YYYY-MM-DD format, defaults to today)',
                },
                messageLimit: {
                  type: 'number',
                  description: 'Maximum number of messages to extract (default: 100)',
                  default: 100,
                },
              },
              required: ['chatName'],
            },
          },
          {
            name: 'send_message_manual',
            description: legacyChatToolDescription(this.runtimePlatform, 'Stage a message draft without sending in the exact named LINE group or direct chat on Windows. Use only when the user asked to review the draft inside LINE.'),
            inputSchema: {
              type: 'object',
              properties: {
                chatName: {
                  type: 'string',
                  description: 'Name of the chat/group to send message to',
                },
                message: {
                  type: 'string',
                  description: 'Message content to send',
                },
              },
              required: ['chatName', 'message'],
            },
          },
          {
            name: 'send_message_auto',
            description: legacyChatToolDescription(this.runtimePlatform, 'Send one approved message immediately in the exact named LINE group or direct chat on Windows. Use only after explicit approval of the exact recipient and message; never retry an uncertain result.'),
            inputSchema: {
              type: 'object',
              properties: {
                chatName: {
                  type: 'string',
                  description: 'Name of the chat/group to send message to',
                },
                message: {
                  type: 'string',
                  description: 'Message content to send',
                },
              },
              required: ['chatName', 'message'],
            },
          }
        ]
      };
    });

    // Handle tool calls
    this.server.setRequestHandler(CallToolRequestSchema, async (request) => {
      const { name, arguments: args } = request.params;

      try {
        if (this.lineExtensions?.handles(name)) return await this.lineExtensions.call(name, args);
        switch (name) {
          case 'get_line_chatroom_history_default':
            return await this.handleGetLineChatroomHistoryDefault(args);

          case 'get_line_chatroom_history_long':
            return await this.handleGetLineChatroomHistoryLong(args);

          case 'get_line_chatroom_history_short':
            return await this.handleGetLineChatroomHistoryShort(args);

          case 'send_message_manual':
            return await this.handleSendMessage(args);

          case 'send_message_auto':
            return await this.handleSendMessageAuto(args);

          default:
            throw new McpError(
              ErrorCode.MethodNotFound,
              `Unknown tool: ${name}`
            );
        }
      } catch (error) {
        if (error instanceof LineToolError || (typeof error.code === 'string' && /^(HISTORY_|LINE_)/.test(error.code))) {
          return {
            isError: true,
            content: [{ type: 'text', text: JSON.stringify({
              success: false,
              code: error.code,
              message: error.message,
              operationMayHaveCompleted: error.operationMayHaveCompleted === true,
            }) }],
          };
        }
        throw new McpError(
          ErrorCode.InternalError,
          `Error executing tool ${name}: ${error.message}`
        );
      }
    });
  }

  async handleGetLineChatroomHistoryDefault(args) {
    const { chatName, date, messageLimit = 100 } = args;
    const targetDate = date || new Date().toISOString().split('T')[0];

    const history = await this.lineAutomation.getChatHistory(chatName, targetDate, messageLimit, 10);
    
   // Ensure history is a string and handle null/undefined cases
   // 限制回應內容長度，避免傳輸問題
   const historyText = (history || '').slice(-50000); // 限制 50KB, 由後往前截取
    
    return {
      content: [
        {
          type: 'text',
          text: JSON.stringify({
            chatName: chatName,
            date: targetDate,
            messageLimit: messageLimit,
            history: historyText,
            chatRoomUpdatedAt: new Date().toLocaleString()
          }, null, 2),
        }
      ],
    };
  }

  async handleGetLineChatroomHistoryLong(args) {
    const { chatName, date, messageLimit = 100 } = args;
    const targetDate = date || new Date().toISOString().split('T')[0];

    const history = await this.lineAutomation.getChatHistory(chatName, targetDate, messageLimit, 50);
    
   // Ensure history is a string and handle null/undefined cases
   // 限制回應內容長度，避免傳輸問題
   const historyText = (history || '').slice(-50000); // 限制 50KB, 由後往前截取
    
    
    return {
      content: [
        {
          type: 'text',
          text: JSON.stringify({
            chatName: chatName,
            date: targetDate,
            messageLimit: messageLimit,
            history: historyText,
            chatRoomUpdatedAt: new Date().toLocaleString(),
          }, null, 2),
        },
      ],
    };
  }

  async handleGetLineChatroomHistoryShort(args) {
    const { chatName, date, messageLimit = 100 } = args;
    const targetDate = date || new Date().toISOString().split('T')[0];

    const history = await this.lineAutomation.getChatHistory(chatName, targetDate, messageLimit, 5);
    
   // Ensure history is a string and handle null/undefined cases
   // 限制回應內容長度，避免傳輸問題
   const historyText = (history || '').slice(-50000); // 限制 50KB, 由後往前截取
    
    
    return {
      content: [
        {
          type: 'text',
          text: JSON.stringify({
            chatName: chatName,
            date: targetDate,
            messageLimit: messageLimit,
            history: historyText,
            chatRoomUpdatedAt: new Date().toLocaleString(),
          }, null, 2),
        },
      ],
    };
  }

  async handleSendMessage(args) {
    const { chatName, message } = args;
    
    const result = await this.lineAutomation.sendChatMessage(chatName, message, false);
    
    return {
      content: [
        {
          type: 'text',
          text: JSON.stringify({
            success: result.success,
            chatName,
            message,
            timestamp: new Date().toISOString(),
            error: result.error || null,
          }, null, 2),
        },
      ],
    };
  }

  async handleSendMessageAuto(args) {
    const { chatName, message } = args;
    
    const result = await this.lineAutomation.sendChatMessage(chatName, message,  true);
    
    return {
      content: [
        {
          type: 'text',
          text: JSON.stringify({
            success: result.success,
            chatName,
            message,
            timestamp: new Date().toISOString(),
            error: result.error || null,
          }, null, 2),
        },
      ],
    };
  }


  async run() {
    const transport = new StdioServerTransport();
    await this.server.connect(transport);
    console.error('LINE Agent MCP Server running on stdio');
  }
}

async function main() {
  // This release exposes only a local stdio transport. Never echo arguments,
  // which could include secrets from an older HTTP-mode configuration.
  if (process.argv.length > 2) {
    console.error('LINE Agent MCP supports stdio only; command-line options are not supported.');
    process.exitCode = 2;
    return;
  }
  const server = new LineDesktopMCPServer();
  console.error('Starting server in stdio mode');
  await server.run();
}

// Importing the server for protocol tests must never run setup or GUI input.
if (process.argv[1] && fs.realpathSync(process.argv[1]) === fs.realpathSync(__filename)) {
  await main();
}
