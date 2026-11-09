// Chat history search (SEARCH=true, Meilisearch): a user finds their own conversation by a word in it, never
// another user's. Temporary users and conversations, no model calls. Run: docker exec -i kai-api-1 node < tests/smoke_chat_search.cjs
require('module-alias').addAlias('~', '/app/api');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const mongoose = require('mongoose');
require('@librechat/data-schemas').createModels(mongoose);
const prefix = `kai-search-${crypto.randomUUID().slice(0, 8)}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await mongoose.connect(process.env.MONGO_URI, { serverSelectionTimeoutMS: 5000 });
  const User = mongoose.model('User'), Conversation = mongoose.model('Conversation'), Message = mongoose.model('Message');
  const made = [];
  try {
    const people = [];
    for (const who of ['a', 'b']) {
      const password = crypto.randomBytes(16).toString('hex'), email = `${prefix}-${who}@example.invalid`;
      const user = await User.create({ email, name: `Search ${who}`, role: 'USER', provider: 'local', emailVerified: true, password: await require('bcryptjs').hash(password, 10) });
      const word = `kata${who}${crypto.randomBytes(4).toString('hex')}`, conversationId = crypto.randomUUID();
      await Conversation.create({ conversationId, user: String(user._id), title: `Catatan ${word}`, endpoint: 'agents' });
      await Message.create({ messageId: crypto.randomUUID(), conversationId, user: String(user._id), text: `pertanyaan tentang ${word}`, isCreatedByUser: true, sender: 'User' });
      made.push(user._id);
      people.push({ email, password, word, conversationId });
    }
    await sleep(3000); // Meilisearch indexes asynchronously
    const login = async (p) => {
      const r = await fetch('http://localhost:3080/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: p.email, password: p.password }) });
      assert.equal(r.status, 200, `login ${r.status}`);
      return (await r.json()).token;
    };
    const search = async (token, q) => {
      const r = await fetch(`http://localhost:3080/api/convos?search=${encodeURIComponent(q)}`, { headers: { Authorization: `Bearer ${token}` } });
      assert.equal(r.status, 200, `search ${r.status}`);
      return ((await r.json()).conversations ?? []).map((c) => c.conversationId);
    };
    const [a, b] = people, ta = await login(a), tb = await login(b);
    assert.deepEqual(await search(ta, a.word), [a.conversationId], 'A finds own conversation');
    assert.deepEqual(await search(ta, b.word), [], "A never finds B's conversation");
    assert.deepEqual(await search(tb, b.word), [b.conversationId], 'B finds own conversation');
    assert.deepEqual(await search(tb, a.word), [], "B never finds A's conversation");
    console.log('Chat search OK: own conversation found, other user\'s hidden.');
  } finally {
    const ids = made.map(String);
    await Message.deleteMany({ user: { $in: ids } });
    await Conversation.deleteMany({ user: { $in: ids } });
    await mongoose.model('Session').deleteMany({ user: { $in: made } });
    await User.deleteMany({ _id: { $in: made } });
    await mongoose.disconnect();
  }
})().then(() => process.exit(0), (e) => { console.error('FAIL:', e.message); process.exit(1); });
