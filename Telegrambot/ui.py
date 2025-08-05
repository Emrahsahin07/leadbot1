import sys
main = sys.modules['__main__']
bot_client = main.bot_client
subscriptions = main.subscriptions
ADMIN_ID = main.ADMIN_ID
save_subscriptions = main.save_subscriptions
categories = main.categories

CANONICAL_LOCATIONS = main.CANONICAL_LOCATIONS
def has_subcats(cat: str) -> bool:
    """Возвращает True, если у категории есть подкатегории в categories.json"""
    return bool(categories.get(cat, {}).get('subcategories'))

from datetime import datetime, timedelta, timezone
from telethon import events, Button
ISTANBUL_TZ = timezone(timedelta(hours=3))
from telethon.errors.rpcerrorlist import MessageNotModifiedError

import time

_START_COOLDOWN = 10  # секунд между /start от одного юзера
_last_start_ts = {}

# Pagination
ITEMS_PER_PAGE = 8

# Helper: Build a toggle menu (checkbox list with Back/Close), with pagination
def build_toggle_menu(title: str, items: list, selected: list,
                     prefix: str, back_key: bytes,
                     page: int = 0):
    """
    Build a toggle menu: each item with a checkbox, plus Back and Close buttons, paginated.
    """
    total_pages = (len(items) - 1) // ITEMS_PER_PAGE + 1
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_items = items[start:end]
    buttons = []
    for it in page_items:
        mark = '✅ ' if it in selected else ''
        buttons.append([Button.inline(f"{mark}{it}", f"{prefix}:{it}")])
    # Pagination navigation
    nav_row = []
    if page > 0:
        nav_row.append(Button.inline('⬅️', f'{prefix}_page:{page-1}'))
    if page < total_pages - 1:
        nav_row.append(Button.inline('➡️', f'{prefix}_page:{page+1}'))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([Button.inline('◀️ Назад', back_key)])
    return title, buttons

# Helper: Safe edit wrapper to ignore MessageNotModifiedError
async def safe_edit(event, *args, **kwargs):
    """Wrapper for event.edit to ignore MessageNotModifiedError when content is unchanged."""
    try:
        await event.edit(*args, **kwargs)
    except MessageNotModifiedError:
        pass

@bot_client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    uid = str(event.sender_id)
    now = time.time()
    if now - _last_start_ts.get(uid, 0) < _START_COOLDOWN:
        return  # игнор повторов
    _last_start_ts[uid] = now
    # Use subcats in prefs defaults
    prefs = subscriptions.get(uid, {'categories': [], 'locations': [], 'subcats': {}})
    cats = prefs.get('categories', [])
    locs = prefs.get('locations', [])
    filters_info = f"🎯 Фильтры: {len(cats)} категорий, {len(locs)} локаций"
    # Determine trial/subscription status
    trial = prefs.get('trial_start')
    sub_end = prefs.get('subscription_end')
    if sub_end:
        end = datetime.fromisoformat(sub_end).astimezone(ISTANBUL_TZ)
        status = f"🛡 Подписка до {end.strftime('%d.%m %H:%M')}"
    elif trial:
        start = datetime.fromisoformat(trial)
        end_dt = start + timedelta(days=2)
        end = end_dt.astimezone(ISTANBUL_TZ)
        status = f"🎁 Пробный до {end.strftime('%d.%m %H:%M')}"
    else:
        status = "🎁 Нет пробного"
    full_header = f"👋 Добро пожаловать!\n{filters_info}\n{status}"
    await event.reply(
        full_header,
        buttons=[
            [Button.inline('🛠 Фильтры', b'menu:settings'),
             Button.inline('💳 Подписка', b'menu:subscribe')],
            [Button.inline('ℹ Справка', b'menu:faq')],
            [Button.inline('❌ Закрыть', b'menu:close')]
        ]
    )

@bot_client.on(events.CallbackQuery)
async def callback(event):
    try:
        data = event.data.decode()
        uid = str(event.sender_id)
        # Initialize or retrieve user preferences, ensuring keys exist
        prefs = subscriptions.setdefault(uid, {'categories': [], 'locations': [], 'subcats': {}})
        # Ensure 'subcats' key exists even for старые записи
        prefs.setdefault('subcats', {})

        # Pagination for categories
        if data.startswith('cat_page:'):
            page = int(data.split(':',1)[1])
            title, buttons = build_toggle_menu(
                'Категории (✅ = выбрано)',
                list(categories.keys()),
                prefs['categories'],
                'cat',
                b'menu:settings',
                page=page
            )
            await safe_edit(event, title, buttons=buttons)
            return

        # Pagination for locations
        elif data.startswith('loc_page:'):
            page = int(data.split(':',1)[1])
            title, buttons = build_toggle_menu(
                'Локации (✅ = выбрано)',
                CANONICAL_LOCATIONS,
                prefs['locations'],
                'loc',
                b'menu:settings',
                page=page
            )
            await safe_edit(event, title, buttons=buttons)
            return

        # Toggle individual subcategory (format subcat:<cat>:<sub>)
        elif data.startswith('subcat:') and '_page:' not in data and data.count(':') == 2:
            _, cat, sub = data.split(':', 2)
            selected = prefs['subcats'].setdefault(cat, [])
            if sub in selected:
                selected.remove(sub)
            else:
                selected.append(sub)

            # Синхронизируем основную категорию: добавляем, если есть хоть одна подкатегория
            if selected and cat not in prefs['categories']:
                prefs['categories'].append(cat)
            # Если все подкатегории сняты — убираем категорию из списка
            if not selected and cat in prefs['categories']:
                prefs['categories'].remove(cat)

            subscriptions[uid] = prefs
            save_subscriptions()
            # Refresh subcategory menu (stay on page 0)
            subcats = list(categories[cat]['subcategories'].keys())
            title, buttons = build_toggle_menu(
                f'Подкатегории «{cat}» (✅ = выбрано)',
                subcats,
                selected,
                f'subcat:{cat}',
                b'menu:settings',
                page=0
            )
            await safe_edit(event, title, buttons=buttons)
            return
        # Main menu callback
        elif data == 'menu:main':
            cats = prefs.get('categories', [])
            locs = prefs.get('locations', [])
            filters_info = f"🎯 Фильтры: {len(cats)} категорий, {len(locs)} локаций"
            # Determine trial/subscription status
            trial = prefs.get('trial_start')
            sub_end = prefs.get('subscription_end')
            if sub_end:
                end = datetime.fromisoformat(sub_end).astimezone(ISTANBUL_TZ)
                status = f"🛡 Подписка до {end.strftime('%d.%m %H:%M')}"
            elif trial:
                start = datetime.fromisoformat(trial)
                end_dt = start + timedelta(days=2)
                end = end_dt.astimezone(ISTANBUL_TZ)
                status = f"🎁 Пробный до {end.strftime('%d.%m %H:%M')}"
            else:
                status = "🎁 Нет пробного"
            full_header = f"👋 Добро пожаловать!\n{filters_info}\n{status}"
            await safe_edit(
                event,
                full_header,
                buttons=[
                    [Button.inline('🛠 Фильтры', b'menu:settings'),
                     Button.inline('💳 Подписка', b'menu:subscribe')],
                    [Button.inline('ℹ Справка', b'menu:faq')],
                    [Button.inline('❌ Закрыть', b'menu:close')]
                ]
            )

        # Settings submenu
        elif data == 'menu:settings':
            await safe_edit(
                event,
                'Настройки фильтров:',
                buttons=[
                    [Button.inline('Категории', b'menu:categories'),
                     Button.inline('Локации', b'menu:locations')],
                    [Button.inline('◀️ Назад', b'menu:main')]
                ]
            )
            # Start 2-day trial for new users
            if 'trial_start' not in prefs:
                prefs['trial_start'] = datetime.now(timezone.utc).isoformat()
                subscriptions[uid] = prefs
                save_subscriptions()
                await event.answer('🎁 Вы активировали 2-дневный пробный период!', alert=True)

        # Categories submenu
        elif data == 'menu:categories':
            selected_cats = [
                cat for cat in categories.keys()
                if cat in prefs['categories'] or prefs['subcats'].get(cat)
            ]
            title, buttons = build_toggle_menu(
                'Категории (✅ = выбрано)',
                list(categories.keys()),
                selected_cats,
                'cat',
                b'menu:settings',
                page=0
            )
            await safe_edit(event, title, buttons=buttons)

        # Toggle category (with subcat opening)
        elif data.startswith('cat:'):
            cat = data.split(':', 1)[1]
            # Если у категории есть подкатегории, открываем их меню
            if has_subcats(cat):
                subcats = list(categories[cat]['subcategories'].keys())
                selected = prefs['subcats'].get(cat, [])
                title, buttons = build_toggle_menu(
                    f'Подкатегории «{cat}» (✅ = выбрано)',
                    subcats,
                    selected,
                    f'subcat:{cat}',
                    b'menu:settings',
                    page=0
                )
                await safe_edit(event, title, buttons=buttons)
                return
            # Обычный toggle для всех категорий
            if cat in prefs['categories']:
                prefs['categories'].remove(cat)
            else:
                prefs['categories'].append(cat)
            subscriptions[uid] = prefs
            save_subscriptions()
            # Refresh categories submenu (stay on page 0)
            selected_cats = [
                c for c in categories.keys()
                if c in prefs['categories'] or prefs['subcats'].get(c)
            ]
            title, buttons = build_toggle_menu(
                'Категории (✅ = выбрано)',
                list(categories.keys()),
                selected_cats,
                'cat',
                b'menu:settings',
                page=0
            )
            await safe_edit(event, title, buttons=buttons)
            return

        # Locations submenu
        elif data == 'menu:locations':
            title, buttons = build_toggle_menu(
                'Локации (✅ = выбрано)',
                CANONICAL_LOCATIONS,
                prefs['locations'],
                'loc',
                b'menu:settings',
                page=0
            )
            await safe_edit(event, title, buttons=buttons)

        # Toggle location
        elif data.startswith('loc:'):
            loc = data.split(':', 1)[1]
            # loc is now canonical display name
            if loc in prefs['locations']:
                prefs['locations'].remove(loc)
            else:
                prefs['locations'].append(loc)
            subscriptions[uid] = prefs
            save_subscriptions()
            # Refresh locations submenu, default to page 0
            title, buttons = build_toggle_menu(
                'Локации (✅ = выбрано)',
                CANONICAL_LOCATIONS,
                prefs['locations'],
                'loc',
                b'menu:settings',
                page=0
            )
            await safe_edit(event, title, buttons=buttons)
            return

        # Close menu
        elif data == 'menu:close':
            await event.delete()

        # Show current filters
        elif data == 'menu:my_filters':
            cats = prefs.get('categories', [])
            locs = prefs.get('locations', [])
            text = (
                "📋 Ваши фильтры:\n"
                f"• Категории: {', '.join(cats) if cats else '—'}\n"
                f"• Локации: {', '.join(locs) if locs else '—'}"
            )
            await safe_edit(event, text, buttons=[[Button.inline('◀️ Назад', b'menu:settings')]])

        # Reset all filters
        elif data == 'menu:reset_filters':
            # Clear user's filters
            prefs['categories'] = []
            prefs['locations'] = []
            prefs['subcats'] = {}
            subscriptions[uid] = prefs
            save_subscriptions()
            # Acknowledge reset
            await event.answer('🔄 Все фильтры сброшены', alert=True)
            # Redisplay settings submenu
            await safe_edit(
                event,
                '⚙ Настройка фильтров:',
                buttons=[
                    [Button.inline('Категории', b'menu:categories'),
                     Button.inline('Локации', b'menu:locations')],
                    [Button.inline('📋 Мои фильтры', b'menu:my_filters'),],
                    [Button.inline('◀️ Назад', b'menu:main'),
                     Button.inline('❌ Закрыть', b'menu:close')]
                ]
            )

        # Show plan info
        elif data == 'menu:plan':
            ts = prefs.get('trial_start')
            if ts:
                start = datetime.fromisoformat(ts)
                end_dt = start + timedelta(days=2)
                end_local = end_dt.astimezone(ISTANBUL_TZ)
                end_str = end_local.strftime('%Y-%m-%d %H:%M (UTC+3)')
                trial_text = f"Пробный период до: {end_str}"
            else:
                trial_text = "Пробный период не активирован"
            text = (
                "🏷 Ваш тариф: Free\n"
                f"{trial_text}\n\n"
                "Чтобы перейти на Pro, нажмите кнопку «Подписка»"
            )
            await safe_edit(
                event,
                text,
                buttons=[[Button.inline('💳 Подписаться', b'menu:subscribe')], [Button.inline('◀️ Назад', b'menu:settings')]]
            )

        # Send a sample lead
        elif data == 'menu:sample':
            example = "🗨 ПримерГруппа | ПримерUser\n— Нужна экскурсия в Кемер?\n\n#кемер #экскурсии"
            await event.answer(example, alert=False)

        # FAQ / Help with structured info
        elif data == 'menu:faq':
            faq_text = (
                "ℹ️ Справка и поддержка\n\n"
                "• Чтобы настроить фильтры: выберите «⚙ Настройки фильтров» → «Категории» или «Локации».\n"
                "• Пробный период — 2 дня, затем подписка:\n"
                "    ◦ 1 мес — 20 USD\n"
                "    ◦ 3 мес — 54 USD\n"
                "    ◦ 6 мес — 96 USD\n"
                "• После оплаты приложите скрин через «✅ Я оплатил».\n"
                "• Техподдержка: support@example.com, @your_support_bot"
            )
            await safe_edit(
                event,
                faq_text,
                buttons=[[Button.inline('◀️ Назад', b'menu:main')]]
            )

        # Show subscription prices and manual payment details merged
        elif data == 'menu:subscribe':
            text = (
                "📦 Доступные подписки:\n"
                "• 1 месяц — 20 USD\n"
                "• 3 месяца — 54 USD\n"
                "• 6 месяцев — 96 USD\n\n"
                "💳 Реквизиты для ручной оплаты:\n"
                "  • Карта: 1234 5678 9012 3456 (Иван Иванов)\n"
                "  • Bitcoin (BTC): 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa\n"
                "  • Ethereum (ETH): 0xAbC1234Ef567890BcDEF1234567890AbCdEF1234\n\n"
                "После оплаты нажмите «✅ Я оплатил» и прикрепите скриншот оплаты.\n"
            )
            await safe_edit(
                event,
                text,
                buttons=[
                    [Button.inline('✅ Я оплатил', b'menu:paid')],
                    [Button.inline('◀️ Назад', b'menu:main')]
                ]
            )

        elif data == 'menu:paid':
            # Notify admin that user pressed “Я оплатил” with their filters
            prefs = subscriptions.get(uid, {})
            cats = prefs.get('categories', [])
            locs = prefs.get('locations', [])
            # Try to get username, otherwise show ID
            try:
                user_entity = await bot_client.get_entity(int(uid))
                uname = user_entity.username or user_entity.first_name or uid
            except:
                uname = uid
            info = (
                f"📢 Пользователь @{uname} (ID={uid}) нажал «Я оплатил» и готов к проверке.\n"
                f"Категории: {', '.join(cats) if cats else '—'}\n"
                f"Локации: {', '.join(locs) if locs else '—'}"
            )
            # Send info to admin
            await bot_client.send_message(ADMIN_ID, info)
            # Prompt user to attach screenshot
            await event.answer("✅ Отлично! Теперь прикрепите скрин оплаты.", alert=True)
            # Mark that we're awaiting payment screenshot from this user
            prefs = subscriptions.get(uid, {})
            prefs['awaiting_screenshot'] = True
            subscriptions[uid] = prefs
            save_subscriptions()

        # Admin approval callbacks
        elif data.startswith('approve:'):
            # Only admin can approve
            if event.sender_id != ADMIN_ID:
                await event.answer("❌ У вас нет прав на это действие", alert=True)
                return
            _, uid_str, months_str = data.split(':')
            months = int(months_str)
            uid = uid_str
            end = datetime.now(timezone.utc) + timedelta(days=30 * months)
            end_local = end.astimezone(ISTANBUL_TZ)
            prefs = subscriptions.setdefault(uid, {})
            prefs['subscription_end'] = end.isoformat()
            # Clear trial flags
            prefs.pop('trial_start', None)
            prefs.pop('trial_expired_notified', None)
            prefs.pop('paid_expired_notified', None)
            # Save updated subscriptions
            save_subscriptions()
            # Notify admin and user with local time
            end_str = end_local.strftime('%Y-%m-%d %H:%M (UTC+3)')
            await safe_edit(event, f"✅ Подписка пользователя {uid} активирована до {end_str}")
            await bot_client.send_message(
                int(uid),
                f"🎉 Ваша подписка активирована до {end_str}!"
            )

        elif data.startswith('reject:'):
            # Only admin can reject
            if event.sender_id != ADMIN_ID:
                await event.answer("❌ У вас нет прав на это действие", alert=True)
                return
            _, uid_str = data.split(':')
            uid = uid_str
            await safe_edit(event, f"❌ Подписка для пользователя {uid} отменена")
            await bot_client.send_message(
                int(uid),
                "К сожалению, оплата не была подтверждена. Попробуйте ещё раз или свяжитесь с поддержкой."
            )

        else:
            await event.answer()
    except MessageNotModifiedError:
        # Ignore if content is the same
        pass
    
@bot_client.on(events.NewMessage(func=lambda e: e.is_private and (e.photo or e.document)))
async def handle_payment_screenshot(event):
    user_id = str(event.sender_id)
    prefs = subscriptions.get(user_id, {})
    # Only handle screenshot if user has pressed "Я оплатил"
    if not prefs.get('awaiting_screenshot'):
        return
    # Clear the flag so future photos won't trigger
    prefs.pop('awaiting_screenshot', None)
    subscriptions[user_id] = prefs
    save_subscriptions()
    # Notify admin via Telethon
    await bot_client.send_message(
        ADMIN_ID,
        f"📸 Получен скрин оплаты от пользователя {user_id}"
    )
    # Forward media via Telethon
    await bot_client.forward_messages(
        ADMIN_ID,
        event.id,
        event.chat_id
    )
    # Present admin with subscription approval options
    await bot_client.send_message(
        ADMIN_ID,
        f"Выберите период подписки для пользователя {user_id}:",
        buttons=[
            [Button.inline("1 мес.", f"approve:{user_id}:1")],
            [Button.inline("3 мес.", f"approve:{user_id}:3")],
            [Button.inline("6 мес.", f"approve:{user_id}:6")],
            [Button.inline("❌ Отказать", f"reject:{user_id}")]
        ]
    )
    # Acknowledge user
    await event.reply("✅ Спасибо, получили ваш скриншот оплаты. Как только проверим — активируем подписку.")