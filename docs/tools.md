# Danh sách tool

[← README](../README.md)

Tra cứu mọi tool MCP theo nhóm. Bạn thường không gọi trực tiếp mà nói với Claude, Claude sẽ chọn tool.

Quy trình phân tích chuẩn: `get_context` (W/D/H4) → `save_bias` → `get_context` (H1/M15) tìm entry → `get_today_status` trước khi vào lệnh.

Không có tool đặt lệnh. Plan của ORB và setup chỉ là con số đề xuất.

## Dữ liệu

| Tool | Việc |
|---|---|
| `list_timeframes` | Các cặp đang theo dõi, khung nào đã có dữ liệu, lần pull gần nhất |
| `refresh_data` | Ép pull ngay từ Binance, không chờ chu kỳ |
| `backfill_klines` | Kéo lịch sử cũ về (lùi tới khi hết dữ liệu hoặc `days` ngày) |
| `get_data_coverage` | Độ phủ dữ liệu từng khung (kể cả 5m), chỗ thiếu nến |
| `get_klines` | Nến OHLCV thô, mặc định chỉ trả nến đã đóng |
| `migrate_klines` | `import` nến JSON sang SQLite, `verify` đối chiếu hai bên ([chi tiết](data.md#lưu-nến-trong-sqlite)) |

## Phân tích

| Tool | Việc |
|---|---|
| `get_context` | Range, vị trí giá trong range, spread/volume cho VSA, swing gần nhất, độ tươi dữ liệu, theo cặp |
| `get_bias` | Đọc bias đã lưu của một cặp |
| `save_bias` | Lưu bias cho một cặp sau bước W/D/H4 |

## Quy định và nhật ký

Chi tiết ở [rules.md](rules.md).

| Tool | Việc |
|---|---|
| `get_rules` | Đọc quy định đang hiệu lực (kèm `symbol` để xem bộ số của một cặp) và lịch sử thay đổi |
| `update_rules` | Đổi quy định, bắt buộc kèm lý do. Rule theo cặp thì kèm `symbol`, `orb=true` đổi bộ rule ORB riêng |
| `get_today_status` | Quota lệnh, PnL, rule đổi hôm nay. Gọi trước khi vào lệnh |
| `check_trade` | Chấm thử một lệnh theo rule mà không ghi nhật ký |
| `log_trade` | Ghi lệnh, cảnh báo nếu phạm rule |
| `close_trade` | Đóng lệnh, cập nhật PnL thực |

## Tài khoản sàn

Cần bật đọc tài khoản, xem [account.md](account.md).

| Tool | Việc |
|---|---|
| `get_positions` | Vị thế đang mở **thật trên sàn**, mọi cặp đang theo dõi |
| `get_fills` | Lệnh đã khớp trong ngày theo từng vị thế, mọi cặp |
| `get_account_pnl` | PnL thật, tách lãi/lỗ, phí, funding và tách theo cặp |
| `reconcile_journal` | Đối chiếu nhật ký tự ghi với sàn, chỉ ra chỗ lệch. `off_plan_pending` là lệnh ngoài kế hoạch chưa đánh giá |

## Lệnh ngoài kế hoạch

Chi tiết ở [off-plan.md](off-plan.md).

| Tool | Việc |
|---|---|
| `get_off_plan_trades` | Lệnh có mã `OP-...`: phân loại, cờ, setup ứng viên, TR, kết quả. Docstring có quy trình "đánh giá lệnh OP-..." |
| `review_off_plan_trade` | Lưu lý do, đánh giá, `verdict`, bài học. Chỉ ghi đè trường có truyền |
| `reclassify_position` | Hệ thống phân loại sai: gắn vị thế vào setup và phân loại lại |

## Setup và kế hoạch

Chi tiết ở [setups.md](setups.md).

| Tool | Việc |
|---|---|
| `save_setups` | Lưu kế hoạch vào lệnh thành setup có cấu trúc. Watcher canh trên nến M5 đã đóng. Bắt buộc `branch` và `entry_event`, tên setup tự sinh ([chi tiết](setups.md#tên-setup-và-nhánh-kịch-bản)) |
| `get_setups` | Setup kèm trạng thái hiện tại, event gần nhất, `label`, `branch`; `trs` là nhánh và xác suất của TR liên kết |
| `get_setup_params` | Tham số setup đang áp dụng cho một cặp, gồm ngưỡng volume gợi ý cho `rejection` |
| `cancel_setup` | Huỷ đúng một setup (bắt buộc lý do) |
| `get_setup_events` | Lịch sử chuyển trạng thái và tin đã gửi, kèm `label`. `PRUNED` / `BRANCH_CONFLICT` / `TR_BRANCH` là event của lần loại setup ngược nhánh |
| `save_scenarios` | Lưu kịch bản giá của một ngày, gọi ngay sau `save_setups`. Kèm `tr` khi tạo TR lần đầu hoặc vẽ lại TR (cùng neo thì gộp vào TR đang hiệu lực) |
| `update_tr` | Cập nhật TR khoá: sơ đồ Wyckoff, trạng thái, creek/ice, pha A–E, thêm và đổi trạng thái event, `prob_evidence`. Gọi một lần, trước `save_bias`. TR xác định nhánh thì tự loại setup ngược nhánh ([chi tiết](dashboard.md#tr-khoá-và-wyckoff), [nhánh](dashboard.md#nhánh-kịch-bản-và-xác-suất)) |
| `delete_tr_event` | Xoá hẳn một event TR tạo nhầm trong 24h (bắt buộc lý do). Gán sai nhãn thì dùng `update_tr` với `status=failed` |

## Biểu đồ và review

Chi tiết ở [dashboard.md](dashboard.md).

| Tool | Việc |
|---|---|
| `get_chart_data` | Một lần gọi đủ dữ liệu vẽ biểu đồ: nến, `vol_spike`, bias, setup, kịch bản, TR (pha, event kèm số VSA, nhánh, xác suất), `prob_history`, marker, mức HTF, vị thế |
| `save_review` | Gắn kết quả review cho một setup. `auto_outcome` của hệ thống giữ nguyên |
| `get_review_stats` | Thống kê setup theo khoảng ngày: tỉ lệ trigger, win rate, R trung bình. Nhóm theo `trigger_type`, `direction`, `symbol`, `phase`, `event_ref_type`, `expects`, `schematic`, `branch`, `entry_event`, hoặc nhóm chéo `direction,event_ref_type,phase` |

## ORB

Chi tiết ở [orb.md](orb.md).

| Tool | Việc |
|---|---|
| `list_orb_sessions` | Các phiên ORB, giờ mở kế tiếp (UTC và giờ VN), watch M5 có đang chạy |
| `get_opening_range` | High/Low của OR (nến M15 đầu phiên), ATR H1, cờ lọc `too_narrow`/`too_wide` |
| `check_orb_signal` | State của phiên, nến M5 kích hoạt, plan entry/SL/TP/qty đã tính phí |
| `skip_orb_session` | Bỏ một phiên trong ngày (bắt buộc lý do), dừng watch M5 |
| `backtest_orb` | Backtest ORB trên M5 lịch sử, tách in-sample/out-of-sample |
| `get_backtest_result` | Lấy kết quả backtest chạy lâu theo `run_id`, dùng cho cả `backtest_time_windows` |
| `get_orb_config` | Tham số và rule ORB đang áp dụng (kèm nguồn từng lớp), cảnh báo `rule_infeasible` |

### Tham số ORB của các tool chung

Bốn tool chung nhận thêm tham số **tuỳ chọn** cho ORB. Không truyền thì hành vi và các khoá trả về giữ nguyên.

- `get_today_status(strategy, session_id)` và `get_account_pnl(strategy, session_id)`: thêm khối lọc theo chiến lược/phiên. Quota và `daily_stop_loss` chung chỉ đếm lệnh **không phải ORB**. ORB có quota và daily stop riêng (`orb_trades_remaining`, `orb_daily_stop_hit`, `can_trade_orb`, luôn có trong `get_today_status`).
- `check_trade(strategy="ORB", session_id, or_date)`: chấm bằng [bộ rule ORB riêng](orb.md#bộ-rule-orb-riêng) (`rule_set: "orb"`, lỗi rule nằm ở `orb_rule_violations`), kèm khối `orb_checks`: còn trong cửa sổ không, phiên còn quota không, range có bị lọc không.
- `log_trade(strategy="ORB", session_id, variant, or_date, or_high, or_low)`: ghi lệnh ORB và dừng watch M5 của phiên đó. Lệnh không phải ORB được gắn `strategy: "OTHER"`.

## GANN

Chi tiết ở [gann.md](gann.md). Chỉ chạy cho cặp đã bật ở `/admin/gann`.

| Tool | Việc |
|---|---|
| `get_pivots` | Pivot swing chart Gann đã được xác nhận đảo chiều |
| `get_swing_state` | Xu hướng, chân sóng đang chạy, overbalance thời gian và giá |
| `get_time_windows` | Những ngày nhiều phép đếm chu kỳ cùng trỏ vào |
| `backtest_time_windows` | Kiểm chứng cửa sổ thời gian trên lịch sử, walk-forward |
| `recompute_pivots` | Tính lại pivot từ đầu và ghi lại cache |
| `get_sq9_levels` | Vùng giá Square of 9 quanh giá hiện tại, kèm lịch sử phản ứng |
