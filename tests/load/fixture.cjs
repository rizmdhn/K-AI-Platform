// P6-07 fixture, run inside the api container: `setup N MSGS` creates N temporary USERs, each with one
// conversation of MSGS messages, and prints {email,id,token,conversationId,word}[] (30-minute JWTs);
// `teardown` removes them. Synthetic text only; no model calls.
require('module-alias').addAlias('~', '/app/api');
const crypto = require('node:crypto');
const mongoose = require('mongoose');
const { createModels, createMethods } = require('@librechat/data-schemas');
createModels(mongoose);
const PREFIX = 'kai-load-';
const [mode, n = '20', msgs = '20'] = process.argv.slice(2);
(async () => {
  await mongoose.connect(process.env.MONGO_URI, { serverSelectionTimeoutMS: 5000, autoIndex: false, autoCreate: false });
  const User = mongoose.model('User'), Conversation = mongoose.model('Conversation'), Message = mongoose.model('Message');
  if (mode === 'setup') {
    const methods = createMethods(mongoose), out = [];
    for (let i = 0; i < +n; i++) {
      const user = await User.create({ email: `${PREFIX}${crypto.randomUUID().slice(0, 8)}@example.invalid`, name: `Load ${i}`, role: 'USER', provider: 'local', emailVerified: true, password: crypto.randomBytes(24).toString('hex') }); // unusable: never hashed, tokens only
      const id = String(user._id), conversationId = crypto.randomUUID(), word = `beban${crypto.randomBytes(4).toString('hex')}`;
      await Conversation.create({ conversationId, user: id, title: `Analisis ${word}`, endpoint: 'agents' });
      let parent = '00000000-0000-0000-0000-000000000000';
      for (let m = 0; m < +msgs; m++) {
        const messageId = crypto.randomUUID();
        await Message.create({ messageId, parentMessageId: parent, conversationId, user: id, isCreatedByUser: m % 2 === 0, sender: m % 2 ? 'K-AI' : 'User',
          text: `Pesan ${m} tentang ${word}: penjualan per wilayah naik ${m}% dibanding bulan lalu. `.repeat(8) });
        parent = messageId;
      }
      out.push({ email: user.email, id, token: await methods.generateToken(user, 30 * 60 * 1000), conversationId, word });
    }
    process.stdout.write(`\nKAI_FIXTURE ${JSON.stringify(out)}\n`); // marker: container logs may share stdout
  } else if (mode === 'teardown') {
    const ids = (await User.find({ email: { $regex: `^${PREFIX}` } }, { _id: 1 }).lean()).map((u) => String(u._id));
    await Message.deleteMany({ user: { $in: ids } });
    await Conversation.deleteMany({ user: { $in: ids } });
    await mongoose.model('Session').deleteMany({ user: { $in: ids } });
    await User.deleteMany({ _id: { $in: ids } });
    process.stdout.write(`removed ${ids.length} load users`);
  }
  await mongoose.disconnect();
})().then(() => process.exit(0), (e) => { console.error(e.message); process.exit(1); });
