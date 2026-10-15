/**
 * Fake adapters for testing. These record calls and return configurable results.
 * The only injection switch allowed is DAILY_AGENDA_ACTIONS_TEST_ADAPTER=fake.
 */

export const createFakeLineAdapter = (options = {}) => {
  const calls = [];
  const {
    sendResult = { status: 'RECORDED_LOCAL', sourceRef: 'message:aabbccddee112233aabbccdd' },
    sendError = null,
    verifyResult = { found: true },
  } = options;

  return {
    calls,
    async send({ chatRef, chatName, chatType, text, nonce }) {
      const call = { method: 'send', chatRef, chatName, chatType, text, nonce, at: new Date().toISOString() };
      calls.push(call);
      if (sendError) throw sendError;
      return { ...sendResult };
    },
    async verify({ chatRef, chatName, chatType, text, committedAt }) {
      const call = { method: 'verify', chatRef, chatName, chatType, text, committedAt, at: new Date().toISOString() };
      calls.push(call);
      return { ...verifyResult };
    },
  };
};

export const createFakeCalendarAdapter = (options = {}) => {
  const calls = [];
  const {
    findSimilarResult = [],
    createResult = { id: 'event-123', status: 'created' },
    createError = null,
    verifyResult = { found: true, id: 'event-123' },
  } = options;

  return {
    calls,
    async findSimilar({ startIso, endIso, subject }) {
      const call = { method: 'findSimilar', startIso, endIso, subject, at: new Date().toISOString() };
      calls.push(call);
      return [...findSimilarResult];
    },
    async create({ subject, startIso, endIso, location, attendees, transactionId }) {
      const call = { method: 'create', subject, startIso, endIso, location, attendees, transactionId, at: new Date().toISOString() };
      calls.push(call);
      if (createError) throw createError;
      return { ...createResult };
    },
    async verify({ transactionId, subject, startIso, endIso }) {
      const call = { method: 'verify', transactionId, subject, startIso, endIso, at: new Date().toISOString() };
      calls.push(call);
      return { ...verifyResult };
    },
  };
};
