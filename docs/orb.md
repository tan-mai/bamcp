# Phân tích ORB

[← README](../README.md)

ORB (Opening Range Breakout) chỉ chạy trên một cặp: `orb.symbol`, mặc định `BTCUSDT`. **OR = nến M15 đầu tiên** của mỗi phiên.

## Dùng trong chat

> *"Phiên New York hôm nay OR thế nào, có tín hiệu chưa?"*

Claude gọi `get_opening_range` → `check_orb_signal`, đọc `trigger_candle` (nến M5 **đã đóng**) và `plan`.

- Plan có `blocked_by` chứa `rules` thì đọc `plan.rule_check.rule_violations`, và không vào.
- Muốn vào lệnh thì `check_trade(strategy="ORB", session_id="ny")`, bạn tự đặt lệnh trên sàn, rồi `log_trade(strategy="ORB", session_id="ny", variant="breakout")`.
- Giá đã chạy xa, không đuổi: `skip_orb_session("ny", reason="...")`.

## Hai giai đoạn

1. **Chốt OR.** Nến M15 đầu phiên đóng + 20 giây (`candle_close_delay_seconds`), scheduler kéo M15 và H1, tính High/Low của OR và ATR(14) của H1 tính đến trước giờ mở. `OR / ATR` dưới `min_or_atr_ratio` là `too_narrow`, trên `max_or_atr_ratio` là `too_wide`. Phiên bị lọc thì **không kéo M5**.
2. **Watch M5.** OR hợp lệ thì cứ 5 phút (nến M5 đóng + 20 giây) kéo M5 một lần, tìm breakout theo `entry.mode`. Watch dừng khi phiên `TAKEN` (đã `log_trade`), `SKIPPED` (đã `skip_orb_session`), hoặc hết `trade_window_minutes`. Ngoài cửa sổ đó không có request M5 nào.

Mọi giờ tính theo timezone IANA của từng phiên nên tự đổi giờ mùa hè: London 08:00 là 07:00Z vào mùa hè, 08:00Z vào mùa đông. Tool trả cả UTC lẫn giờ VN.

State của một phiên trong một ngày:

```
WAITING_OPEN → FORMING → RANGE_SET → BREAKOUT_LONG / BREAKOUT_SHORT
                                    → FAILED_BREAKOUT_LONG / FAILED_BREAKOUT_SHORT
kết thúc: FILTERED · EXPIRED · SKIPPED · TAKEN
```

Bộ lọc khác: `use_bias_filter` (so với bias đã `save_bias` trong ngày), `skip_news_days` (ngày có tin lớn), quota `max_trades` mỗi phiên và `orb.max_trades_per_day` (đếm theo `or_date`).

## Plan

Plan tính sẵn phí taker và trượt giá hai chiều vào 1R.

- `qty` = `orb.margin_usd × orb.leverage / entry`, làm tròn **xuống** theo `qty_step`: 20 × 100 / 83,500 → 0.023 BTC.
- Dưới `min_notional_usd` thì plan có cảnh báo, BAMCP không tự tăng size.
- Mỗi plan còn được chấm bằng rule ORB (`plan.rule_check`, `filters.rules`). Trượt rule hoặc đã chạm daily stop ORB thì `blocked_by` chứa `rules`: plan vẫn trả về để xem vì sao, nhưng không phải lệnh để vào.

## Bộ rule ORB riêng

Lệnh ORB có SL/TP nhỏ hơn hẳn scalp/swing, nên được chấm bằng một bộ rule riêng: khối `orb` trong `data/rules.json`. `check_trade`, `log_trade`, `check_orb_signal`, `get_today_status` và `backtest_orb` đều dùng chung một hàm giải rule nên không lệch nhau.

| Rule | BTC | Việc |
|---|---|---|
| `max_stop_points` | 350 | SL tối đa |
| `min_take_profit_points` | 200 | TP tối thiểu |
| `min_rr` | 1.5 | R:R tối thiểu (TP / SL) |
| `max_trades_per_day` | 2 | Quota lệnh ORB mỗi `or_date`, riêng, không ăn quota chung |
| `daily_stop_loss` | -25 | PnL ORB trong `or_date` chạm ngưỡng thì chặn ORB, lệnh khác vẫn vào được |
| `margin_usd` × `leverage` | 20 × 100 | Sizing của plan |
| `maint_margin_pct`, `liq_safety_pct` | 0.4, 80 | SL phải nằm trong 80% khoảng cách tới giá thanh lý (≈354 điểm ở 83,500) |

Tham số chiến lược cũng nằm ở đây: `min_or_atr_ratio` (0.3), `max_or_atr_ratio` (0.7), `tp_r` (1.5), `buffer_pct` (0.02), `move_sl_to_be_at_r`, `time_exit_minutes`, `use_bias_filter`, `allow_reversal`, `skip_news_days`.

**Ba lớp, lớp trên đè lớp dưới:** **phiên** (chỉ tham số chiến lược) → **cặp** (`symbol_overrides`) → **`orb.defaults`**. `get_orb_config` ghi rõ mỗi giá trị lấy từ lớp nào (`source`). Cặp chưa đặt SL/TP ORB thì lệnh ORB bị chặn với lỗi `chua dat rule ORB cho <CẶP>.<field>`, server không tự đoán ngưỡng.

**Công tắc nghiệp vụ:** `orb.enabled = false` trong rules thì không phiên nào chốt OR/watch M5, `check_orb_signal` trả `orb_disabled`, `check_trade(strategy="ORB")` báo ORB đang tắt.

### Sửa rule ORB

Có hai đường. Cả hai bắt buộc lý do, mỗi trường đổi là một dòng `history` (nguồn `mcp` hoặc `admin`), có hiệu lực ngay.

- **Chat:** `update_rules({"min_rr": 2}, reason="...", symbol="BTCUSDT", orb=true)`. Bỏ trống `symbol` là sửa `orb.defaults`, `session_id="ny"` là override của phiên, giá trị `null` là bỏ override.
- **Trang admin:** mục **Rule ORB** trên `/admin/orb`: chọn phạm vi, sửa, xem trước bộ rule đã giải kèm cảnh báo trước khi lưu. Mục này hiện luôn số lệnh ORB đã dùng và PnL ORB hôm nay.

### Cảnh báo `rule_infeasible`

Theo ATR H1 và giá hiện tại, server kiểm xem bộ số có tự mâu thuẫn không. Ví dụ `min_take_profit_points` 900 với `tp_r` 1.5 và `max_or_atr_ratio` 1.2 ở ATR 462 thì TP tối đa chỉ ~832, mọi tín hiệu đều trượt. Hoặc OR tối đa + buffer đã vượt `max_stop_points`.

Cảnh báo hiện trong `get_orb_config.warnings`, trên trang admin và ngay ở bước xem trước. Nó không chặn lưu.

## Cấu hình

**Tham số kỹ thuật** nằm ở khối `orb:` trong `config.yaml` (sửa xong phải restart): `entry.mode`, `failed_lookback_bars`, `exit.sl_mode`, khung và chu kỳ ATR, costs, `exchange_limits`, đường dẫn dữ liệu. Bật/tắt nhanh cả module (scheduler, tool, trang admin) bằng `BAMCP_ORB_ENABLED=true|false`.

**Rule và tham số chiến lược** nằm trong `rules.json`, xem [Bộ rule ORB riêng](#bộ-rule-orb-riêng).

### Danh sách phiên

Quản lý ở `/admin/orb`, lưu trong `data/orb/sessions.json`, **không cần restart**. `default_sessions` trong config chỉ là hạt giống cho lần chạy đầu. Trên trang đó:

- Thêm/sửa phiên: `session_id`, tên, timezone, giờ mở, ngày giao dịch, cửa sổ, quota, override kỹ thuật (vd `entry_mode`). Override **rule** của phiên (OR/ATR, `tp_r`, `buffer_pct`...) sửa ở mục Rule ORB: form phiên giữ nguyên chúng và từ chối nếu bị sửa ở đây.
- **Xem trước** giờ mở kế tiếp theo UTC và giờ VN trước khi lưu.
- Bật/tắt, xoá phiên. Tắt hay xoá giữa phiên thì watch đang chạy dừng ngay.
- Lịch sử thay đổi.

`session_id` không đổi được sau khi tạo, vì state, nhật ký và backtest đều gắn vào nó.

### Ngày có tin lớn

Thêm/xoá ở mục **News days** trên `/admin/orb`. File là `data/orb/news_days.json` dạng `[{"date": "2026-10-02", "note": "CPI"}]` (dạng cũ `["2026-10-02"]` vẫn đọc được), đọc lại mỗi lần tính, không cần restart. `skip_news_days = true` thì phiên của ngày đó bị lọc (`news_day: true`, `filtered: true`), không ra plan.

## Backtest

> *"Backtest ORB 2 năm, lấy 2026-03-01 làm mốc out-of-sample."*

`backtest_orb(from_date, to_date, session_ids, split_date, overrides, initial_equity, rules_override)` chạy trên M5 lịch sử.

- **Dữ liệu:** lần đầu tải M5 từ `data.binance.vision` (file tháng, tháng đang chạy ghép từ file ngày, có kiểm SHA256), khoảng nửa phút cho 2 năm. Sau đó đọc cache dưới một giây.
- **Chạy lâu:** quá ~55 giây thì tool trả `status: running` kèm `run_id`, gọi `get_backtest_result(run_id)` sau.
- **Kết quả:** số lệnh, win rate, expectancy theo R, profit factor, drawdown, chuỗi thua dài nhất, tách in-sample/out-of-sample và theo phiên, lý do thoát (tp/sl/breakeven/time), số tín hiệu bị lọc hoặc không vào được vì quy mô tài khoản, và `data_gaps` cho những ngày thiếu dữ liệu.
- **Quy ước:** nến M5 chạm cả SL lẫn TP thì tính là **thua** (`sl_tp_same_bar`).
- **File:** `trades.csv`, `equity.csv`, `result.json` nằm trong `data/orb/backtests/<run_id>/`.

Mỗi lệnh giả lập được chấm bằng rule ORB hiện hành, kể cả quota và daily stop ORB theo ngày. Kết quả có `rules_used` (bộ rule đã dùng), `rejected_by_rule` (số lệnh bị loại theo từng rule) và `stopped_days` (ngày chạm daily stop ORB). `rules_override`, vd `{"min_take_profit_points": 900}`, thử bộ số khác cho riêng lần chạy đó, không sửa `rules.json`.

## Tương thích bản cũ

Lần đầu chạy bản có rule ORB riêng, khối `orb` được tạo **một lần** từ giá trị cũ trong `config.yaml` (nếu còn) và mặc định BTC ở trên, kèm một dòng `history` nguồn `migrate`. Các field cũ trong `config.yaml` (`risk.*`, `exit.tp_r`, `filters.max_or_atr_ratio`, `max_orb_trades_per_day`...) từ đó bị bỏ qua và được liệt kê trong `get_orb_config.warnings` dạng `deprecated: ...`. Xoá chúng khỏi `config.yaml` là hết cảnh báo.
