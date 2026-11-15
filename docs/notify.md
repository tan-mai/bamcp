# Kênh thông báo theo cặp

[← README](../README.md)

Mỗi cặp có kênh riêng: một topic Telegram hoặc một channel Discord. Dòng đầu mọi tin cho biết ngay cặp nào, loại tin gì, lúc mấy giờ. Tin mức thấp được lọc, chống trùng và gom lại.

Chưa cấu hình gì thì hành vi giữ như cũ: kênh đang dùng trở thành **kênh mặc định**, mọi tin vẫn vào đó, chỉ thêm dòng đầu có tên cặp.

Mọi nơi gửi tin (watcher M5, off-plan, cảnh báo hệ thống) đều đi qua một cửa `notifier.send(symbol, category, severity, title, body, dedupe_ref=None, embed_fields=None)`. Lỗi gửi tin không bao giờ làm dừng watcher hay fetcher.

## Dòng đầu tin

```
{emoji cặp} {SYM} · {emoji loại} {LOẠI TIN} · {HH:mm}
```

```
🟠 BTC · 🎯 ENTRY TRIGGERED · 09:15
🔷 ETH · ⚠️ OFF-PLAN · 08:27
🟡 XAU · 📍 IN ZONE · 14:40
⚙️ SYSTEM · ❌ FETCHER ERROR · 10:02
```

Thân tin giữ nguyên như trước. Dòng đầu luôn có, kể cả khi tin đã vào kênh riêng của cặp, để tin fallback hay tin forward vẫn đọc được.

- Telegram: dòng đầu in đậm, gửi vào đúng topic (`message_thread_id`).
- Discord: dòng đầu nằm ở `content` (thông báo đẩy trên điện thoại hiện đúng dòng này), thân tin nằm trong embed mang màu của cặp. `username` mặc định `BAMCP · BTC`, đặt riêng theo kênh được.
- Emoji và màu của cặp sửa ở trang admin. Mặc định BTC 🟠 `#F7931A`, ETH 🔷 `#627EEA`, XAU 🟡 `#D4AF37`. Cặp khác dùng 🔹.

## Loại tin và mức độ

Mức độ: `low < info < high < critical`.

| category | Nguồn | Mặc định |
|---|---|---|
| `ENTRY_IN_ZONE` | Watcher M5: giá vào vùng | info |
| `ENTRY_TRIGGERED` | Watcher M5: có trigger | high |
| `ENTRY_CONFIRMED` | Watcher M5: `ENTRY_VALID`, hoặc `BLOCKED` (check_trade không qua) | high |
| `ENTRY_MISSED` | Watcher M5: `MISSED`, hết cửa sổ entry | low |
| `ZONE_EXIT` | Watcher M5: trigger hỏng, quay lại chờ vùng | low |
| `ENTRY_FILLED` | Vị thế khớp setup | info |
| `SETUP_INVALIDATED` | `INVALIDATED`, `EXPIRED`, `CANCELLED`, `PRUNED`, tin `TR_BRANCH` | info (`BRANCH_CONFLICT` và `TR_BRANCH` có vị thế ngược nhánh: high) |
| `OFF_PLAN` | Lệnh ngoài kế hoạch, thêm vị thế ngoài kế hoạch, thiếu SL | high (`COUNTER_PLAN` hoặc cờ `REVENGE`: critical) |
| `TRADE_RESULT` | Vị thế off-plan đóng | info |
| `ORB_SIGNAL` | Dành sẵn cho ORB | high |
| `BIAS_SAVED` | Dành sẵn cho save_bias | low |
| `SYSTEM_ERROR` | `FETCHER_STALE`, kênh thông báo lỗi liên tục | high |
| `SYSTEM_NOTICE` | `PLAN_MISSING` | info |

Hiện ORB và `save_bias` chưa gửi tin nào. Hai loại `ORB_SIGNAL` và `BIAS_SAVED` có sẵn trong bảng route để dùng sau.

Tin không gắn được cặp (`PLAN_MISSING`, `FETCHER_STALE`, lỗi kênh) dùng `symbol = "*"`.

## Chọn kênh

Tin `(symbol, category, severity)` đi qua các cấp sau:

1. Route khớp `symbol` + `category`
2. Route khớp `symbol` + `*`
3. Route khớp `*` + `category`
4. Kênh mặc định (`notify.default_channel_id`)

Dừng ở cấp đầu tiên có route đang bật. Một ô có thể trỏ tới nhiều kênh, khi đó tin gửi song song ra tất cả các kênh đó.

Tin có `severity` thấp hơn `min_severity` của route bị bỏ qua và ghi `FILTERED`. Tin đó **không** rơi xuống cấp sau.

Kênh System là route `*` + `*`. Với tin `symbol = "*"`, cấp 2 chính là ô này. Tin của một cặp cụ thể không bao giờ khớp `*` + `*`.

## Giảm số tin

**Chống trùng.** Khoá là `{symbol}:{category}:{ref}`, trong đó ref là id setup kèm tên event, hoặc id lệnh off-plan kèm loại tin. Cùng khoá trong `notify.dedupe_window_seconds` (mặc định 300 giây) thì không gửi lại và ghi `DEDUPED`. Ngoại lệ: tin mới có severity cao hơn tin trước. Ví dụ giá ra vào vùng 4 lần trong 5 phút chỉ cho 1 tin `ENTRY_IN_ZONE`.

**Gom tin.** Route nào bật "gom tin" thì tin `low` (và `info` nếu bật `digest_include_info`) gom lại theo kênh và cặp. Cứ mỗi `notify.digest_minutes` (mặc định 15 phút, theo mốc đồng hồ) gửi một tin tóm tắt. Tin `high` và `critical` luôn gửi ngay.

```
🟠 BTC · 🗂 TÓM TẮT 09:00–09:15
• IN ZONE: BTC 15/11 · SHORT · UT sau AR#1 (09:03)
• MISSED: BTC 15/11 · SHORT · UT sau AR#1 (09:11)
• SETUP INVALIDATED: BTC 15/11 · LONG · Spring (09:12)
```

Kênh mặc định gom tin khi bật `digest_default`.

**Giờ yên lặng.** `notify.quiet_hours` (ví dụ `23:00-05:00`, theo `display_timezone`). Trong khung giờ này chỉ tin `critical` gửi ngay. Tin khác vào tin tóm tắt, gửi lúc hết giờ yên lặng.

**Giới hạn tốc độ.** Mỗi kênh tối đa 1 tin/giây. Các topic chung một group Telegram dùng chung hạn mức 20 tin/phút. Vượt hạn mức thì tin xếp hàng chờ, không bị bỏ. Telegram hoặc Discord trả 429 thì cả group dừng đúng `retry_after` giây rồi gửi lại.

Trạng thái chống trùng và tin tóm tắt đang chờ nằm trong bộ nhớ. Khởi động lại server thì có thể lọt thêm một tin trùng, và mất các tin `low` đang chờ gom.

## Khi kênh lỗi

- Gửi lỗi thì thử lại 3 lần, sau 2s, 5s và 15s.
- Vẫn lỗi thì gửi sang kênh mặc định, thêm dòng `⚠️ FALLBACK (kênh {tên} lỗi)` ở trên tin gốc, ghi `FALLBACK`. Kênh mặc định cũng lỗi thì ghi `FAILED`.
- Một kênh lỗi liên tiếp từ 5 lần trở lên trong 10 phút thì có một tin `SYSTEM_ERROR` vào kênh System. Mỗi kênh lỗi tối đa 1 tin này mỗi giờ.
- Thiếu giá trị token hoặc webhook là lỗi cấu hình: không thử lại, chuyển thẳng sang fallback.
- `setup_events.notify_error` và `off_plan_*.notify_error` ghi lỗi khi có kênh không nhận được tin. Tin đã tới tay qua fallback thì không tính là lỗi.

## Token và webhook

Mọi cài đặt kênh, kể cả bot token và webhook URL, nhập ở `/admin/notify`. Server không đọc `.env` hay biến môi trường cho phần này, nên deploy bằng panel hay bằng compose đều như nhau.

- Bot token và webhook URL lưu trên volume, trong khối `notify_secrets` của `alerts/config.json`. DB không lưu giá trị: cột `telegram_bot_token_ref` / `discord_webhook_ref` chỉ giữ khoá do server tự sinh (ví dụ `TG_3F9A1C2B`).
- Sửa kênh mà để trống ô token / webhook thì giữ giá trị đang lưu.
- Kênh Telegram mới để trống bot token thì dùng chung bot của kênh Telegram đã có. Các kênh dùng chung bot hiện "dùng chung với ...". Nhập token khác cho một kênh thì chỉ kênh đó đổi, kênh kia giữ bot cũ.
- Bot token phải dạng `123456789:AA...`. Webhook phải bắt đầu bằng `https://discord.com/api/webhooks/`.
- Xoá kênh thì token / webhook không còn kênh nào dùng cũng bị xoá khỏi volume.

Trang admin, `notify_log` và output tool MCP chỉ hiện "đã nhập" kèm 4 ký tự cuối (`****abcd`). Lỗi trả về từ Telegram hoặc Discord cũng được xoá token trước khi ghi.

## Trang `/admin/notify`

- **Kênh**: thêm, sửa, xoá, bật/tắt. Nút **Gửi thử** gửi `🧪 TEST · {tên kênh} · {giờ}` thẳng vào kênh đó, không thử lại. Bảng hiện trạng thái lần gửi cuối (OK hoặc lỗi kèm nội dung lỗi).
- **Route**: ma trận cặp × loại tin. Bấm một ô để chọn kênh (chọn nhiều được), `min_severity`, bật gom tin, bật/tắt.
- **Cài đặt chung**: kênh mặc định, `dedupe_window_seconds`, `digest_minutes`, gom tin `info`, gom tin ở kênh mặc định, giờ yên lặng, `log_retention_days`, giờ hiển thị, emoji và màu theo cặp. Có nút gửi ngay tin tóm tắt đang chờ.
- **Nhật ký**: 200 dòng `notify_log` mới nhất, lọc theo cặp và trạng thái.
- Khi đã có route theo cặp mà một cặp đang theo dõi chưa có route riêng (ví dụ vừa thêm cặp mới), trang hiện gợi ý kèm nút "Tạo route".

Trang `/admin/alerts` chỉ còn một ô tóm tắt kèm link sang trang này. Mục Off-plan không còn ô "Kênh": kênh của tin off-plan do route `* + OFF_PLAN` quyết định.

## Tool MCP

| Tool | Việc |
|---|---|
| `get_notify_config` | Kênh (đã che bí mật), route, cài đặt chung, bảng loại tin, thứ tự chọn route |
| `test_notification(symbol, category="TEST")` | Gửi tin thử qua đúng route sẽ chọn, trả về kênh đã dùng (`channels_used`, `route`). Không thử lại, không fallback |
| `get_notify_log(symbol, status, limit=50)` | Tra tin đã gửi, bị lọc, bị gom, lỗi |
| `get_today_status` | Thêm khối `notifications` {`by_symbol`: {cặp: {sent, filtered, deduped, digested, fallback, failed}}, `totals`} |

## Lưu trữ

SQLite, khoá chính là GUID:

- `notify_channels`: `provider`, `telegram_bot_token_ref`, `telegram_chat_id`, `telegram_thread_id`, `discord_webhook_ref`, `discord_username`, `discord_avatar_url`, `enabled`, `last_ok_at`, `last_error`, `last_error_at`.
- `notify_routes`: `symbol`, `category` (`*` = tất cả), `channel_id`, `min_severity`, `digest`, `enabled`.
- `notify_log`: `symbol`, `category`, `severity`, `channel_id`, `dedupe_key`, `status` (`SENT` / `FILTERED` / `DEDUPED` / `DIGESTED` / `FAILED` / `FALLBACK`), `error`, `payload_preview` (200 ký tự đầu), `created_at`. Giữ `notify.log_retention_days` ngày (mặc định 30). Tin tóm tắt ghi `category = DIGEST`.

Cài đặt chung nằm trong khối `notify` của `alerts/config.json` (giá trị khởi tạo ở `config.yaml`).

## Chuyển từ cấu hình cũ

Chạy lúc khởi động, đánh dấu `notify.legacy_channels_imported` khi xong:

1. Tạo 3 bảng mới.
2. Chép **mọi** kênh đã nhập ở `/admin/alerts` bản trước, bất kể `notify.channel` đang chọn kênh nào: "Discord (kênh cũ)" với webhook cũ, "Telegram (kênh cũ)" với bot token và `chat_id` cũ. Telegram thiếu `chat_id` hoặc thiếu token thì bỏ qua và ghi lý do vào log khởi động.
3. Chưa đặt kênh mặc định thì kênh mặc định là kênh cũ đang dùng (`notify.channel` = `discord`, `telegram` hoặc `both`), nên tin vẫn về đúng chỗ cũ. Kênh cũ còn lại vẫn được tạo và bật, nhưng chưa nhận tin cho tới khi tick làm mặc định hoặc gán route.
4. Không tạo route nào ngoài off-plan: `off_plan.channel` cũ khác kênh chung thì tạo route `* + OFF_PLAN` và `* + TRADE_RESULT` trỏ tới kênh đó.
5. Chạy lại an toàn: kênh cùng webhook, hoặc cùng bot token và `chat_id`, đã có thì dùng lại, không tạo thêm. Sau khi đánh dấu, kênh xoá tay không bị tạo lại.
6. Khối `notify.discord` / `notify.telegram` cũ giữ nguyên, nên quay về image cũ vẫn gửi được tin.

## Tạo kênh

### Telegram: một supergroup, mỗi cặp một topic

1. Tạo group mới → Settings → bật **Topics** (group tự thành supergroup).
2. Tạo các topic `🟠 BTC`, `🔷 ETH`, `🟡 XAU`, `⚙️ System`.
3. Thêm bot hiện tại vào group và cấp quyền **Admin** để bot gửi được tin trong topic.
4. Gửi một tin bất kỳ trong mỗi topic. Mở `https://api.telegram.org/bot<TOKEN>/getUpdates`, lấy `chat.id` (dạng `-100...`) và `message_thread_id` của từng topic.
5. Ở `/admin/notify` tạo mỗi topic một kênh: cùng `chat_id`, khác topic id. Kênh đầu tiên nhập bot token, các kênh sau để trống ô token để dùng chung bot. Bấm **Gửi thử** từng kênh.
6. Route: `BTCUSDT + *` → topic BTC, `ETHUSDT + *` → ETH, `XAUUSDT + *` → XAU, `* + *` → System.

Mỗi cặp một group riêng (khác `chat_id`), hoặc mỗi cặp một bot riêng (nhập token khác), cũng chạy được.

### Discord: mỗi cặp một channel

1. Tạo các channel `#btc-alerts`, `#eth-alerts`, `#xau-alerts`, `#bamcp-system`.
2. Với mỗi channel: Edit Channel → Integrations → Webhooks → New Webhook → Copy URL.
3. Ở `/admin/notify` tạo kênh Discord, dán URL vào ô "Webhook URL", bấm **Gửi thử**.
4. Gán route như bước Telegram.

Một route chọn cả kênh Telegram lẫn Discord thì mỗi sự kiện ra đúng 2 tin, mỗi kênh 1 tin.
