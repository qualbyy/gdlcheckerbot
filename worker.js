export default {
  async fetch(request, env) {
    if (request.method === "GET") {
      return new Response("GDL Checker Bot Worker is running!", { status: 200 });
    }

    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    const botToken = env.BOT_TOKEN;
    if (!botToken) {
      return new Response("BOT_TOKEN is not configured", { status: 500 });
    }

    try {
      const update = await request.json();
      if (!update || !update.message) {
        return new Response("OK", { status: 200 });
      }

      const msg = update.message;
      const text = (msg.text || "").trim();
      const chatId = msg.chat?.id;
      const msgId = msg.message_id;

      if (!text.startsWith("/") || !chatId) {
        return new Response("OK", { status: 200 });
      }

      const parts = text.split(/\s+/);
      const cmd = parts[0].split("@")[0].toLowerCase();
      const arg = parts.slice(1).join(" ").trim();

      if (cmd === "/start" || cmd === "/help") {
        const helpText = 
          "👋 Привет! Я GDL Checker Bot, работающий на Cloudflare Workers.\n\n" +
          "Доступные быстрые команды:\n" +
          "🏆 /top — Топ-10 игроков Demonlist\n" +
          "👤 /player [ник] — Информация об игроке\n" +
          "📜 /level [название или позиция] — Информация об уровне\n" +
          "❓ /help — Список команд";
        await sendMessage(botToken, chatId, helpText, msgId);
      } else if (cmd === "/top") {
        await handleTop(botToken, chatId, msgId);
      } else if (cmd === "/player") {
        if (!arg) {
          await sendMessage(botToken, chatId, "Использование: /player [ник игрока]", msgId);
        } else {
          await handlePlayer(botToken, chatId, arg, msgId);
        }
      } else if (cmd === "/level") {
        if (!arg) {
          await sendMessage(botToken, chatId, "Использование: /level [название или номер позиции]", msgId);
        } else {
          await handleLevel(botToken, chatId, arg, msgId);
        }
      }

      return new Response("OK", { status: 200 });
    } catch (err) {
      console.error("Worker error:", err);
      return new Response("OK", { status: 200 });
    }
  }
};

async function sendMessage(token, chatId, text, replyToId = null) {
  const url = `https://api.telegram.org/bot${token}/sendMessage`;
  const body = {
    chat_id: chatId,
    text: text,
    parse_mode: "HTML",
    disable_web_page_preview: true
  };
  if (replyToId) body.reply_to_message_id = replyToId;

  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function handleTop(token, chatId, replyToId) {
  try {
    const res = await fetch("https://pointercrate.com/api/v2/players/ranking/?limit=10");
    if (!res.ok) {
      await sendMessage(token, chatId, "❌ Не удалось получить топ игроков с Demonlist.", replyToId);
      return;
    }
    const players = await res.json();
    let text = "🏆 <b>Топ-10 игроков Demonlist:</b>\n\n";
    players.forEach((p, index) => {
      const rank = p.rank || (index + 1);
      const name = escapeHtml(p.name);
      const score = Math.round((p.score || 0) * 100) / 100;
      const nation = p.nationality ? ` [${p.nationality.nation}]` : "";
      text += `<b>${rank}.</b> ${name}${nation} — <b>${score}</b> pts\n`;
    });
    await sendMessage(token, chatId, text, replyToId);
  } catch (err) {
    await sendMessage(token, chatId, "❌ Ошибка при запросе к серверу Demonlist.", replyToId);
  }
}

async function handlePlayer(token, chatId, query, replyToId) {
  try {
    const res = await fetch(`https://pointercrate.com/api/v2/players/ranking/?name_contains=${encodeURIComponent(query)}&limit=1`);
    if (!res.ok) {
      await sendMessage(token, chatId, `❌ Ошибка при поиске игрока "${escapeHtml(query)}".`, replyToId);
      return;
    }
    const list = await res.json();
    if (!list || list.length === 0) {
      await sendMessage(token, chatId, `🔍 Игрок "<b>${escapeHtml(query)}</b>" не найден в топе Demonlist.`, replyToId);
      return;
    }

    const p = list[0];
    const rank = p.rank ? `#${p.rank}` : "Вне топа";
    const score = Math.round((p.score || 0) * 100) / 100;
    const nation = p.nationality ? `${p.nationality.nation}` : "Не указана";

    const text = 
      `👤 <b>Игрок:</b> ${escapeHtml(p.name)}\n` +
      `🏅 <b>Ранг:</b> ${rank}\n` +
      `⭐ <b>Очки:</b> ${score}\n` +
      `🌍 <b>Страна:</b> ${escapeHtml(nation)}`;

    await sendMessage(token, chatId, text, replyToId);
  } catch (err) {
    await sendMessage(token, chatId, "❌ Ошибка при получении профиля игрока.", replyToId);
  }
}

async function handleLevel(token, chatId, query, replyToId) {
  try {
    let url = "";
    if (/^\d+$/.test(query)) {
      url = `https://pointercrate.com/api/v2/demons/listed/?position=${query}`;
    } else {
      url = `https://pointercrate.com/api/v2/demons/listed/?name_contains=${encodeURIComponent(query)}&limit=1`;
    }

    const res = await fetch(url);
    if (!res.ok) {
      await sendMessage(token, chatId, `❌ Ошибка при поиске уровня "${escapeHtml(query)}".`, replyToId);
      return;
    }
    const list = await res.json();
    if (!list || list.length === 0) {
      await sendMessage(token, chatId, `🔍 Уровень "<b>${escapeHtml(query)}</b>" не найден на Demonlist.`, replyToId);
      return;
    }

    const lvl = list[0];
    const publisher = lvl.publisher ? lvl.publisher.name : "Неизвестен";
    const verifier = lvl.verifier ? lvl.verifier.name : "Неизвестен";
    const req = lvl.requirement ? `${lvl.requirement}%` : "100%";

    const text = 
      `📜 <b>Уровень:</b> #${lvl.position} — <b>${escapeHtml(lvl.name)}</b>\n` +
      `👤 <b>Создатель/Паблишер:</b> ${escapeHtml(publisher)}\n` +
      `🎯 <b>Верификатор:</b> ${escapeHtml(verifier)}\n` +
      `📊 <b>Требование для списка:</b> ${req}\n` +
      `🔗 <a href="https://pointercrate.com/demonlist/permalink/${lvl.id}/">Страница на Pointercrate</a>`;

    await sendMessage(token, chatId, text, replyToId);
  } catch (err) {
    await sendMessage(token, chatId, "❌ Ошибка при получении информации об уровне.", replyToId);
  }
}
