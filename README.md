# Amul stock alert

Checks [Amul Chocolate Whey Protein, 30 sachets](https://shop.amul.com/en/product/amul-chocolate-whey-protein-34-g-or-pack-of-30-sachets)
(code `WPCCP03_01`) every 15 minutes and messages you on WhatsApp (and optionally Telegram)
when it comes back in stock.

## Setup (about 5 minutes)

### 1. Get your CallMeBot key (WhatsApp)
1. Save **+34 644 51 95 23** in your phone contacts (any name, e.g. "CallMeBot").
2. On WhatsApp, send it this exact message: `I allow callmebot to send me messages`
3. It replies with your **API key** (a number). Keep it.

If that number doesn't reply, check https://www.callmebot.com/blog/free-api-whatsapp-messages/ for the current one.

### 2. Add secrets on GitHub
In this repo: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value | Needed? |
|---|---|---|
| `WHATSAPP_PHONE` | your number with country code, e.g. `+919876543210` | Yes |
| `CALLMEBOT_API_KEY` | the key from step 1 | Yes |
| `PINCODE` | your 6-digit delivery pincode | Recommended: Amul shows stock per area |
| `TELEGRAM_BOT_TOKEN` | token from @BotFather | Optional backup |
| `TELEGRAM_CHAT_ID` | your chat id | Optional backup |

**Optional Telegram backup** (in case CallMeBot is down):
1. In Telegram, message **@BotFather**, send `/newbot`, follow the steps, copy the token.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the number after `"chat":{"id":`.

### 3. Test it
**Actions → Check Amul stock → Run workflow**, tick **Send test message**, press **Run workflow**.
You should get a message within a minute.

### 4. Check that it can read the page
Run the workflow once more **without** ticking the box. Open the run → **Check stock** step.
You want to see `Status: out (via plain)` or `Status: out (via browser)`.
If it says `Status: unknown`, the bot can't read Amul's page yet; see "Limitations".

That's it. It now runs on its own every 15 minutes.

## How it works
1. Downloads the page and looks for the product code. If the stock status isn't there
   (for example, the shop needs JavaScript or a pincode first), it opens the page in a
   real headless browser, enters your pincode, and reads the result, including the data
   the page loads in the background.
2. Reads "In Stock" / "Sold Out" right after the product code, so labels on other
   products don't confuse it. If signals disagree, it assumes **out of stock** (no false alarms).
3. Sends **one** message when the product goes from out of stock to in stock. If it
   sells out and restocks again, you get another.
4. If the message fails to send, it tries again on the next check instead of forgetting.
5. If the page can't be read, it sends nothing. After 2 hours of failures you get one ⚠️
   warning, and one ✅ message when it recovers.
6. Saves its memory in `state.json`, and touches it once a month so GitHub doesn't pause
   the schedule for inactivity.

## Limitations
- **Delay:** checks every 15 minutes, and GitHub often starts scheduled runs 5–20 minutes
  late. Popular restocks can sell out before you hear about it.
- **Not yet tested on the live site.** The logic is tested on sample pages; step 4 above
  is the real test.
- **Site changes or blocking:** if Amul redesigns the page or blocks GitHub's servers, the
  bot reports "unknown" and warns you after 2 hours. It never sends a false restock.
- **CallMeBot** is a free hobby service with no guarantee. Add Telegram as a backup.
- **Public repo:** anyone can see which product you're watching and when it changed.
  Your number, key and pincode are hidden secrets.
- It only alerts you. You still buy it yourself.

## Stopping it
**Actions → Check Amul stock → ⋯ → Disable workflow**.

## Run the tests
```
pip install -r requirements.txt pytest
python -m playwright install chromium
python -m pytest tests
```
