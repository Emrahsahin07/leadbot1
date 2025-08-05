import logging
from telethon import Button
from datetime import datetime, timezone, timedelta
from filters import extract_stems
from config import bot_client, ADMIN_ID, categories, subscriptions, save_subscriptions, metrics, logger


async def send_lead_to_users(chat_id, group_name, group_username, sender_name, sender_id, sender_username, text, link, region, detected_category=None, **kwargs):
    failed_uids = []
    # Send to each user based on their subscriptions
    for uid_str, prefs in subscriptions.items():
        try:
            uid = int(uid_str)
        except ValueError:
            continue
        now = datetime.now(timezone.utc)
        # Debug trial/subscription state
        logger.debug(f"[DEBUG TRIAL] User {uid_str}: subscription_end={prefs.get('subscription_end')}, trial_start={prefs.get('trial_start')}, now={now.isoformat()}")
        # Check paid subscription first
        sub_end = prefs.get('subscription_end')
        if sub_end:
            end = datetime.fromisoformat(sub_end)
            if now > end:
                # Paid subscription expired: notify user once
                if not prefs.get('paid_expired_notified'):
                    await bot_client.send_message(
                        uid,
                        "⌛ Ваша подписка закончилась. Чтобы продолжить получать лиды, нажмите кнопку:",
                        buttons=[[Button.inline("Подписаться", b"menu:subscribe")]]
                    )
                    prefs['paid_expired_notified'] = True
                    # Save updated subscriptions
                    save_subscriptions()
                metrics['sub_expired_skipped'] += 1
                continue
        else:
            # No paid subscription: check trial
            ts = prefs.get('trial_start')
            if not ts:
                # Trial not started yet
                continue
            start = datetime.fromisoformat(ts)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if now - start > timedelta(days=2):
                # Trial expired: notify user once
                if not prefs.get('trial_expired_notified'):
                    await bot_client.send_message(
                        uid,
                        "⌛ Ваш пробный период закончился. Чтобы продолжить получать лиды, нажмите кнопку:",
                        buttons=[[Button.inline("Подписаться", b"menu:subscribe")]]
                    )
                    prefs['trial_expired_notified'] = True
                    # Save updated subscriptions
                    save_subscriptions()
                metrics['trial_expired_skipped'] += 1
                continue
        keywords = []
        # Stems from выбранных категорий
        for cat in prefs.get("categories", []):
            keywords.extend(extract_stems(categories.get(cat, {})))

        # Stems from выбранных подкатегорий
        for cat, sub_list in prefs.get("subcats", {}).items():
            for sub in sub_list:
                sub_entry = categories.get(cat, {}).get("subcategories", {}).get(sub, {})
                keywords.extend(extract_stems(sub_entry))

        keywords = [str(k) for k in keywords]
        locations = prefs.get("locations", [])
        # Check if this message matches the user's region subscription
        if region not in locations:
            metrics['pref_region_skipped'] += 1
            logger.debug(f"Skipping user {uid}: region {region} not in their locations {locations}")
            continue
        # --- strict AI‑category filter ---------------------------------
        # Отправляем лид только если AI определил категорию
        # и она входит в подписку пользователя.
        if detected_category and detected_category not in prefs.get("categories", []):
            metrics['pref_ai_category_skipped'] += 1
            logger.debug(f"Skipping user {uid}: AI category '{detected_category}' "
                         f"not in user's categories {prefs.get('categories')}")
            continue
        # Check if any stem from subscribed categories appears in the text
        if not any(kw.lower() in text.lower() for kw in keywords):
            metrics['pref_category_skipped'] += 1
            logger.debug(f"Skipping user {uid}: none of their category stems {keywords} found in text '{text}'")
            continue
        # Build clickable group name using username if available
        if group_username:
            chat_url = f"https://t.me/{group_username}"
        else:
            # Fallback: strip message ID from link
            if link and link.startswith("https://t.me/"):
                parts = link.rsplit("/", 1)
                chat_url = parts[0] if len(parts) == 2 else link
            else:
                chat_url = ""
        if chat_url:
            group_display = f'<a href="{chat_url}">{group_name}</a>'
        else:
            group_display = group_name
        region_tag = f"#{region.lower()}" if region else ""
        # Use AI-detected category if provided, fallback to subscriber's first category
        if detected_category:
            ai_category_tag = f"#{detected_category.lower()}"
        else:
            cats = prefs.get("categories", [])
            ai_category_tag = f"#{cats[0].lower()}" if cats else ""
        msg = (
            f"🗨 {group_display} | {sender_name}\n\n"
            f"- {text}\n\n"
            f"{region_tag} {ai_category_tag}".strip()
        )
        # Build a row with both "Сообщение" and "Пользователь" buttons
        if link:
            # Link to user profile: by username if available, else by ID
            user_url = f"https://t.me/{sender_username}" if sender_username else f"tg://user?id={sender_id}"
            buttons = [[
                Button.url("Сообщение", link),
                Button.url("Пользователь", user_url)
            ]]
        else:
            buttons = None
        # Send message with the constructed buttons
        try:
            await bot_client.send_message(
                uid,
                msg,
                parse_mode="HTML",
                link_preview=False,
                buttons=buttons
            )
            metrics['leads_sent'] += 1
            logger.info(f"Lead sent to user {uid}")
        except Exception as e:
            metrics['send_errors'] += 1
            failed_uids.append(uid)
            logger.error(f"Failed to send lead to {uid}: {e}")
    # Notify admin if any sends failed
    if failed_uids:
        try:
            await bot_client.send_message(
                ADMIN_ID,
                f"⚠️ Ошибка рассылки лида пользователям: {len(failed_uids)} не удалось отправить. UIDs: {failed_uids}"
            )
        except Exception as notify_error:
            logger.error(f"Failed to notify admin about send errors: {notify_error}")