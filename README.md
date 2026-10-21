# Amul stock alert

Checks [Amul Chocolate Whey Protein, 30 sachets](https://shop.amul.com/en/product/amul-chocolate-whey-protein-34-g-or-pack-of-30-sachets)
(code `WPCCP03_01`) every 15 minutes and sends you a WhatsApp message when it comes back in stock.

## Setup (about 5 minutes)

### 1. Get your CallMeBot key
1. Save **+34 644 51 95 23** in your phone contacts (any name, e.g. "CallMeBot").
2. On WhatsApp, send it this exact message: `I allow callmebot to send me messages`
3. It replies with your **API key** (a number). Keep it.

If that number doesn't reply, check https://www.callmebot.com/blog/free-api-whatsapp-messages/ for the current one.

### 2. Add two secrets on GitHub
In this repo: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `WHATSAPP_PHONE` | your number with country code, e.g. `+919876543210` |
| `CALLMEBOT_API_KEY` | the key from step 1 |

### 3. Test it
**Actions → Check Amul stock → Run workflow**, tick **Send test message**, press **Run workflow**.
You should get a WhatsApp message within a minute.

That's it. The check now runs on its own every 15 minutes.

## How it works
- Finds the product code on the page and reads "In Stock" / "Out of Stock" right after it,
  so "Sold Out" badges on other products don't confuse it.
- Sends **one** message when the product goes from out of stock to in stock. If it sells out
  and restocks again, you get another.
- If the page can't be read, it sends nothing. After 8 failed checks in a row (2 hours)
  you get one ⚠️ warning.
- The last result is stored in `state.json`, which the bot updates only when something changes.

## Notes
- GitHub's 15-minute schedule can run a few minutes late when GitHub is busy.
- GitHub pauses scheduled workflows in repos with no activity for 60 days. If you get an
  email about that, open the Actions tab and press **Enable workflow**.
- To stop the bot: **Actions → Check Amul stock → ⋯ → Disable workflow**.

## Run the tests
```
python -m pytest tests
```
