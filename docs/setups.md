# Setup vào lệnh và cảnh báo

[← README](../README.md)

AI chỉ lập kế hoạch, việc canh giá chạy hoàn toàn trên server. Cuối buổi phân tích bias, Claude gọi `save_setups(symbol, setups, replace=true)` cho từng cặp. Từ đó watcher xét từng setup trên nến **đã đóng** và gửi một tin mỗi lần setup đổi trạng thái.

Mã setup: `<base>-<yyyyMMdd>-<chữ cái>`, vd `BTC-20261101-A`. Tool nhận id hoặc code.

## Thiết lập sau khi deploy

1. Ở `/admin/alerts`, điền kênh thông báo và bấm **Gửi thử**.
2. Phía Claude: dời task phân tích bias sang 00:05 UTC và kết thúc bằng `save_setups(..., replace=true)` cho từng cặp.
3. Khi hỏi "vào được chưa", Claude đọc `get_setups` và `get_setup_events` trước, rồi mới đọc nến M5/M15 đã đóng.

## Watcher chạy thế nào

- Chạy ở giây thứ 20 sau mỗi mốc 5 phút.
- Với cặp có setup đang chạy, watcher tự kéo lại nến 5m vừa đóng từ Binance (kèm nến 15m ở mốc 15 phút nếu setup có `m15_filter`), vì bản do lịch `pull-5m` chụp ở giây thứ 5 có thể chưa chốt giá đóng và volume.
- Kéo lỗi hoặc chưa có nến thì thử lại sau 10 giây, tối đa 3 lần. Hết lượt thì xét trên dữ liệu đang có và ghi vào `errors` của lần quét.
- Khởi động lại thì xét bù tối đa 12 nến, chỉ gửi tin cần báo mới nhất.

## Trạng thái của setup

| Trạng thái | Sang | Khi | Tin |
|---|---|---|---|
| WAITING | IN_ZONE | high/low của nến giao với vùng | chỉ lần chạm đầu tiên của setup |
| IN_ZONE | WAITING | đóng ra ngoài vùng về phía cũ, chưa trigger | không |
| IN_ZONE | TRIGGERED | trigger đạt | có nếu có bước confirm |
| TRIGGERED | CONFIRMED | đóng vượt `confirm_level` trong `confirm_within` nến; không có confirm thì sang ngay | — |
| TRIGGERED | WAITING | quá `confirm_within` nến chưa confirm | "trigger hỏng" |
| CONFIRMED | ENTRY_VALID | \|close − entry\| ≤ max_chase và check_trade pass | **tín hiệu vào lệnh** |
| CONFIRMED | MISSED | giá đã vượt entry hơn max_chase | "không đuổi" |
| CONFIRMED | BLOCKED | check_trade fail | kèm lý do (không gửi lại cùng lý do trong ngày) |
| MISSED | ENTRY_VALID | giá quay lại trong max_chase và check_trade pass | có |
| ENTRY_VALID | WAITING | hết `entry_valid_candles` nến chưa khớp | "hết cửa sổ entry" |
| TRIGGERED…MISSED | FILLED | vị thế OKX mới cùng hướng, hoặc `log_trade(setup_id=...)` | có |
| chưa kết thúc | INVALIDATED / EXPIRED / CANCELLED | đóng quá `invalidate_level` (kèm M15 nếu `m15_filter`) / quá `expires_at` / `cancel_setup` hoặc `replace=true` | có |

**Chạm lại vùng.** Giá ra khỏi vùng rồi quay lại thì không gửi tin IN_ZONE nữa (event vẫn ghi, `details.repeat = true`), nhưng trigger vẫn được xét nên TRIGGERED / ENTRY_VALID vẫn báo. Setup đã INVALIDATED thì bỏ qua hoàn toàn, giá quay lại vùng cũng không làm gì.

## Trigger

Các loại: `spring`, `utad`, `sos`, `sow`, `rejection`, `retest`, `close_above`, `close_below`.

Trigger được xét ngay trên nến chạm vùng, rồi trên mỗi nến M5 đã đóng khi còn IN_ZONE. `vol_ok` nghĩa là volume ≥ `vol_mult` × SMA(`sma_period`) volume M5. Thiếu `sma_period` nến lịch sử thì không xét, ghi event `INSUFFICIENT_DATA`.

Điều kiện volume M5 theo trigger:

| Trigger | Điều kiện volume |
|---|---|
| `spring`, `utad`, `sos`, `sow` | ≥ `vol_mult` |
| `retest` | ≤ `retest_low_vol_mult` |
| `rejection` | Khai riêng từng setup bằng `trigger_params.max_vol_mult` (test, vol thấp) và/hoặc `min_vol_mult` (stopping action, vol cao). Bắt buộc có ít nhất một ngưỡng |
| `close_above`, `close_below` | Không xét volume |

Lưu ý:

- Lúc lưu setup không kiểm tra volume, tin IN_ZONE cũng không. Volume chỉ xét lúc trigger, và tin IN_ZONE ghi rõ điều kiện đang áp dụng.
- `trigger_params` chỉ nhận tham số mà trigger đó đọc, khai thừa thì `save_setups` từ chối (xem docstring).
- Điều kiện viết trong `note` không có tác dụng.

## Tham số theo cặp

Thứ tự ưu tiên: `trigger_params` của setup, rồi `setup_params` của cặp (bảng SQLite), rồi `watcher.defaults`.

- Mỗi lần khởi động, cặp nào chưa có dòng trong bảng thì được ghi từ khối `setup_params` của `config.yaml`. Tham số từng chỉnh tay trong `alerts/config.json` được giữ.
- Dòng đã có không bị ghi đè, kể cả khi sửa `config.yaml`, nên muốn đổi thì sửa ở `/admin/alerts` (có hiệu lực ngay).
- `rejection_test_max_vol` và `rejection_stopping_min_vol` chỉ là số gợi ý. Watcher không đọc trực tiếp, Claude lấy từ `get_setup_params` rồi khai vào `trigger_params` của setup.

## Hướng khung lớn

Lúc lưu setup, server tự ghi `htf` = `{w, d, h4, bias}`.

- W/D/H4 tính từ swing của nến đã đóng (cùng nguồn `get_context`): `up` khi đỉnh và đáy cao dần, `down` khi đỉnh và đáy thấp dần, còn lại `range`. Đóng vượt swing vừa rồi ngược hướng thì về `range`. Thiếu dữ liệu thì `null`.
- `bias` lấy từ `save_bias` của `bias_date`, nên phải `save_bias` trước `save_setups`.
- `htf_counter` liệt kê các khung ngược hướng setup, không chặn lưu. Tin IN_ZONE, TRIGGERED, ENTRY_VALID, MISSED, BLOCKED và trang admin hiện nhãn `NGƯỢC ...` cùng dòng `Khung lớn`.

## Liên kết TR khoá

Setup chỉ trỏ tới TR, không chép lại cấu trúc Wyckoff. Pha và sự kiện nằm trên TR ([dashboard.md](dashboard.md#tr-khoá-và-wyckoff)).

- `tr_id`: bỏ trống là TR đang hiệu lực của cặp. TR không tồn tại hoặc của cặp khác thì setup bị từ chối.
- `event_ref`: event setup đang giao dịch, dạng `TYPE#n` (`TEST#3`) hoặc `TYPE`. Được phép trỏ tới event chưa xảy ra.
- `expects`: event kỳ vọng nếu setup đúng, ví dụ `LPS`.
- `phase`: server tự ghi `current_phase` của TR lúc tạo setup.
- `tr_high`, `tr_low`, `tr_since` lấy từ TR liên kết. Khai khác TR thì bị thay bằng số của TR và trả `warnings`. Không có TR thì giữ số khai như trước.
- `event_ref` hoặc `expects` không khớp `schematic` của TR (ví dụ `UTAD` trên TR tích luỹ) chỉ cảnh báo.

Thẻ setup trên dashboard hiện thêm một dòng, ví dụ "Pha C · giao dịch TEST 3 · kỳ vọng LPS".

## Lúc nào `save_setups` từ chối

**`max_chase` không còn khả thi.** `max_chase` hiệu lực = min(khai báo, x_max), với x_max giữ R:R tại giá vào thực tế ≥ `min_rr` của cặp. Với lệnh long: `((T−E) − m(E−S)) / (1+m)`. x_max ≤ 0 thì `save_setups` từ chối setup đó. Ví dụ E 67500, S 67050, T 68400, m 1.5 → 90.

**`invalidate_level` đã bị phá.** Tại giá hiện tại (long: giá ≤ mức, short: giá ≥ mức, lấy giá đóng mới nhất) thì `save_setups` từ chối, vì lưu vào cũng bị huỷ ngay nến đầu. Setup breakout có `invalidate_level` nằm bên kia giá thì khai `confirm_type`: cờ `invalidate_after_confirm` mặc định bật, mức này chỉ được xét sau khi đã có nến đóng đạt `confirm_level` (chốt vào `state.confirm_reached`, không mất đi).

## Thông báo

`notify.channel` = `discord` (webhook), `telegram` (bot, `parse_mode=HTML`) hoặc `both`.

- Gửi lỗi thì thử lại sau 2s, 10s, 30s. Discord 429 thì chờ đúng `retry_after`. Vẫn lỗi thì ghi `notify_error`, không chặn watcher.
- Cảnh báo hệ thống, mỗi loại tối đa 1 lần/giờ: `PLAN_MISSING` (tới `plan_deadline_utc` mà cặp trong `watch_symbols` chưa có setup hôm nay) và `FETCHER_STALE` (quá 15 phút không có nến 5m mới).

## Khớp lệnh OKX

Mỗi lượt quét, cặp có setup từ TRIGGERED trở đi thì đọc vị thế. Vị thế mới mở sau lúc trigger, cùng hướng → FILLED cho setup có entry gần giá mở nhất. Không setup nào khớp → tin "lệnh mở ngoài kế hoạch". Mã OKX theo `watcher.okx_symbol_map`.

## Trang `/admin/alerts`

- Kênh, webhook, bot token, chat id (che, chỉ ghi đè khi nhập mới). Nút Gửi thử Discord / Gửi thử Telegram dùng cấu hình đã lưu, thử riêng từng kênh, không retry.
- Watcher bật/tắt, `watch_symbols`, `plan_deadline_utc`, bảng tham số mặc định và override theo cặp.
- Danh sách setup theo ngày/trạng thái, timeline event, huỷ, form tạo setup tay.

Tham số theo cặp lưu vào bảng `setup_params`, phần còn lại vào `alerts/config.json`, đều có hiệu lực ngay.

## Tương thích bản cũ

- Setup tạo trước khi có hướng khung lớn thì không có nhãn `NGƯỢC ...`.
- Setup lưu trước khi có `invalidate_after_confirm` giữ hành vi cũ.
