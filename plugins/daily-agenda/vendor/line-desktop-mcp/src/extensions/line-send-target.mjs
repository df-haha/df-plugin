import { readPlainIdentity } from './line-plain-send.mjs';
import { LineToolError, requireChat, requireChoice } from './line-runtime.mjs';
import { readLocalLineBoundDirectIdentity } from './line-local-reader.mjs';
import { boundDirectScope } from './line-bound-direct.mjs';

const REFUSALS = new Set(['CHAT_AMBIGUOUS', 'CHAT_NOT_FOUND', 'CHAT_TYPE_MISMATCH', 'CHAT_IDENTITY_CHANGED', 'CHAT_ACCOUNT_CHANGED']);
const FLAGS = Object.freeze({ readOnly: true, guiVerified: false, sendDispatched: false });

/** A current local uniqueness check, never send approval or a reusable UI proof. */
export async function checkLineSendTarget({ chatName, chatType, expectedChatRef, expectedOwnSenderRef },
  { readIdentity, readBoundIdentity = readLocalLineBoundDirectIdentity, now = () => new Date() } = {}) {
  requireChat(chatName);
  requireChoice(chatType, 'chatType', ['direct', 'group']);
  if (expectedChatRef !== undefined && !/^chat:[0-9a-f]{24}$/u.test(expectedChatRef)) {
    throw new LineToolError('LINE_INVALID_ARGUMENT', 'expectedChatRef must be an opaque chat reference.');
  }
  let identity;
  let blockedCode;
  const boundScope = expectedOwnSenderRef === undefined ? null : boundDirectScope({chatName,chatType,expectedChatRef,expectedOwnSenderRef},now().valueOf());
  try {
    if (boundScope) {
      const value=await readBoundIdentity(boundScope);
      if(value.chatRef!==expectedChatRef)throw new LineToolError('CHAT_IDENTITY_CHANGED','');
      if(value.ownSenderRef!==expectedOwnSenderRef)throw new LineToolError('CHAT_ACCOUNT_CHANGED','');
      if(value.chatIdentity?.kind!=='direct' || value.chatIdentity.knownNameUnique!==true
        || typeof value.chatIdentity.globalNameUnique!=='boolean'
        || value.count!==0 || value.messages?.length!==0)throw new Error('Unverified bound identity');
      return {status:value.chatIdentity.globalNameUnique?'IDENTITY_UNIQUE':'IDENTITY_BOUND_REQUIRES_UI',
        chatRef:expectedChatRef,ownSenderRef:expectedOwnSenderRef,kind:'direct',checkedAt:now().toISOString(),...FLAGS};
    }
    // This is exactly the cross-kind, unopened-contact uniqueness check used
    // by plain-text send. The identity reader never selects message rows.
    identity = await readPlainIdentity({ chatName }, { readIdentity });
    if (identity.kind !== chatType) blockedCode = 'CHAT_TYPE_MISMATCH';
    else if (expectedChatRef !== undefined && identity.chatRef !== expectedChatRef) blockedCode = 'CHAT_IDENTITY_CHANGED';
  } catch (error) {
    if (REFUSALS.has(error?.code)) blockedCode = error.code;
    else throw new LineToolError('LINE_IDENTITY_CHECK_FAILED',
      'Local send-target identity could not be checked.', FLAGS);
  }
  const checkedAt = now().toISOString();
  return blockedCode
    ? { status: 'BLOCKED', code: blockedCode, checkedAt, ...FLAGS }
    : { status: 'IDENTITY_UNIQUE', chatRef: identity.chatRef, kind: identity.kind, checkedAt, ...FLAGS };
}
