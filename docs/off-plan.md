# Lệnh ngoài kế hoạch

[← README](../README.md)

`position_watcher` đối chiếu mỗi vị thế mới mở trên sàn với kế hoạch setup trong ngày. Lệnh lệch kế hoạch có một mã `OP-{yyyymmdd}-{SYM}-{n}` và một tin Discord/Telegram ngay sau khi khớp. Tan mở Claude với mã đó để cùng đánh giá, kết quả lưu lại để thống kê.

Chỉ cảnh báo và ghi nhận: không chặn lệnh, không đóng lệnh. Cần bật [đọc tài khoản sàn](account.md).

## Cách chạy

- Mỗi `off_plan.poll_seconds` (mặc định 15 giây) đọc **toàn bộ fill trong ngày** của từng cặp đang theo dõi, gom theo vị thế như `get_fills`, và đọc vị thế đang mở.
- Vị thế chưa từng thấy → `OPENED`: phân loại và gửi tin nếu ngoài kế hoạch. Lệnh mở mới cùng chiều → `ADDED`. Vị thế đóng → `CLOSED`: tin kết quả với PnL thật.
- Fill từng phần của cùng một lệnh là một chân, không phải lệnh thêm. Lệnh có order id khác mới là lệnh thêm.
- Đọc sàn lỗi thì bỏ qua cặp đó trong lần poll, ghi log một lần, **không gửi cảnh báo**. Lần đọc thành công sau đọc lại cả ngày nên vị thế mở trong lúc lỗi được quét bù, tin ghi rõ "phát hiện trễ N phút".
- Watcher setup M5 vẫn chuyển setup sang `FILLED` như cũ. Khi `position_watcher` đang chạy, tin `UNPLANNED_POSITION` cũ không gửi nữa để không báo hai lần.

## Phân loại

Setup ứng viên: cùng cặp, `bias_date` là ngày mở vị thế (hoặc setup ngày khác còn hạn), **trạng thái tại lúc mở vị thế** dựng lại từ `setup_events`. Setup `CANCELLED` bị bỏ qua. Quét bù hay phát lại fill cũ vẫn chấm theo kế hoạch lúc đó, không theo trạng thái bây giờ.

| Mã | Khi nào | Gửi tin |
|---|---|---|
| `IN_PLAN` | Khớp setup `TRIGGERED`/`CONFIRMED`/`ENTRY_VALID`, giá vào trong `entry ± max_chase` | Không (chỉ khi phạm rule hoặc thiếu SL) |
| `EARLY` | Setup cùng hướng còn `WAITING`/`IN_ZONE`, giá trong vùng hoặc gần entry | Có |
| `CHASE` | Setup đã trigger nhưng giá vào lệch quá `max_chase`, hoặc setup đã `MISSED` | Có |
| `OUT_OF_ZONE` | Setup cùng hướng chưa trigger, giá ngoài vùng và xa entry | Có |
| `COUNTER_PLAN` | Không có setup cùng hướng, có setup đang chạy hướng ngược lại | Có |
| `NO_PLAN` | Không có setup nào cho cặp đó hôm nay (kèm lý do ORB nếu có) | Có |
| `PRUNED_SETUP` | Chỉ khớp setup đã `PRUNED`/`INVALIDATED`/`EXPIRED`/`FILLED` | Có |

Nhiều setup cùng hướng: setup tiến xa nhất thắng (`ENTRY_VALID` > `CONFIRMED` > `TRIGGERED` > `IN_ZONE` > `WAITING`), rồi entry gần giá vào nhất.

`log_trade(setup_id=...)` trong vòng 1 giờ quanh lúc mở thì lấy đúng setup đó, bỏ auto-match. ORB: vị thế khớp phiên ORB trong trade window, đúng hướng phá vỡ, chưa skip → `IN_PLAN` (`rule_set: "orb"`).

### Cờ

| Cờ | Ý nghĩa |
|---|---|
| `NO_STOP` | Sau `no_stop_grace_seconds` (mặc định 60) sàn không có lệnh SL cho vị thế. Gửi một tin bổ sung |
| `REVENGE` | Mở trong `revenge_window_minutes` (mặc định 30) sau khi một vị thế khác đóng lỗ |
| `RULE_BREACH` | Cùng hàm với `check_trade`, dùng giá vào, SL/TP và margin **thật**. Thiếu SL/TP thì bỏ qua rule cần con số đó |
| `AGAINST_TR_BRANCH` | TR đã xác định nhánh và vị thế ngược nhánh |
| `REPEAT_OFF_PLAN` | Lệnh ngoài kế hoạch thứ 2 trở lên trong ngày (mọi cặp) |
| `SIZE_OVER_PLAN` | Margin vượt "Gợi ý vốn N% hạn mức" ghi trong note setup khớp |
| `ADD_OFF_PLAN` | Lệnh thêm không theo kế hoạch chia entry |

`REVENGE` hoặc `REPEAT_OFF_PLAN` thêm dòng đầu `🛑 Lệnh ngoài kế hoạch thứ N hôm nay — cân nhắc dừng giao dịch`.

SL/TP thật đọc từ lệnh algo đang chờ trên OKX (TP/SL đính kèm, `oco`, lệnh trigger) và TP/SL đặt cho cả vị thế. Sàn khác chưa đọc được nên không gắn `NO_STOP`.

### Quy ước trong note setup

Hai câu này trong `note` của setup được watcher đọc (không phân biệt dấu):

- `Gợi ý vốn 80% hạn mức` hoặc `Gợi ý vốn tối đa 30% hạn mức`: tỉ lệ so với `max_margin_per_trade` → cờ `SIZE_OVER_PLAN` khi margin thật vượt quá 5%.
- `Chia entry: 1/2 khi nến SOW đóng, 1/2 chờ retest LPSY 70635–70800`: lệnh thêm có giá trong vùng retest là hợp lệ. Không ghi vùng thì dùng vùng của setup. Lệnh thêm khớp đúng một setup khác đang `IN_PLAN` cũng hợp lệ.

## Tin

```
🛑 Lệnh ngoài kế hoạch thứ 2 hôm nay — cân nhắc dừng giao dịch
⚠️ LỆNH NGOÀI KẾ HOẠCH · OP-20261113-BTC-2
BTCUSDT · LONG · vào 70550.2 · margin — · 09:26:13
Phân loại: COUNTER_PLAN — kế hoạch BTC hôm nay chỉ có SHORT:
  • BTC 13/11 · SHORT · PHÂN PHỐI · Pha B · SOW sau ST_B#1 · 70300–70635.2 (WAITING)
Cờ: REVENGE (21 phút sau OP-20261113-BTC-1 đóng lỗ −34.97) · RULE_BREACH (...) · REPEAT_OFF_PLAN
TR M15: Pha B · PP 83% / TL 17%
👉 Mở Claude: "đánh giá lệnh OP-20261113-BTC-2"
```

Khi đóng: `OP-20261113-BTC-1 đã đóng · PnL thật −34.97 (phí −7.28) · R thực tế −1.49 · chưa đánh giá`. PnL thật là `realized_pnl` của sàn, chưa trừ phí. R tính theo SL trên sàn; không có SL thì ghi "không có SL nên không tính được R".

Lệnh trong kế hoạch nhưng phạm rule hoặc thiếu SL cũng có mã OP và tin tiêu đề "TRONG KẾ HOẠCH NHƯNG VI PHẠM RULE".

## Đánh giá cùng Claude

Nhắn Claude `đánh giá lệnh OP-...`. Claude làm theo quy trình trong docstring của `get_off_plan_trades`: lấy lệnh, kế hoạch và TR lúc đó, đọc nến M15/M5 **đã đóng tại lúc vào lệnh**, hỏi lý do, chấm theo checklist vào lệnh M15, kết luận `verdict` và bài học rồi lưu bằng `review_off_plan_trade`.

| `verdict` | Nghĩa |
|---|---|
| `valid_unplanned` | Cơ hội hợp lệ chưa có trong kế hoạch, cân nhắc thêm loại setup này (`add_to_plan`) |
| `discipline_breach` | Vi phạm kỷ luật |
| `execution_error` | Lỗi thao tác |
| `misclassified` | Hệ thống phân loại sai, dùng `reclassify_position` |

## Tool

| Tool | Việc |
|---|---|
| `get_off_plan_trades(date, status, symbol, code)` | Lệnh có mã OP kèm phân loại, cờ, setup ứng viên, TR, kết quả, timeline |
| `review_off_plan_trade(code, ...)` | Lưu `tan_reason`, `claude_assessment`, `verdict`, `lesson`, `add_to_plan`. Chỉ ghi đè trường có truyền |
| `reclassify_position(position_id, setup_id)` | Gắn vị thế vào setup và phân loại lại, ghi event `RECLASSIFIED` |

Thay đổi ở tool cũ:

- `get_today_status`: khối `off_plan` {count, codes, pending_review, pending_codes, pnl, open, by_classification, in_plan_breach}.
- `get_review_stats`: khối `plan_compare` so sánh trong kế hoạch và ngoài kế hoạch (số lệnh, win rate, tổng PnL, R trung bình), theo `classification` và `verdict`.
- `log_trade(setup_id=...)`: vị thế vừa bị đánh ngoài kế hoạch thì tự gắn vào setup đó và phân loại lại (khối `off_plan`).
- `reconcile_journal`: `off_plan_pending` liệt kê lệnh ngoài kế hoạch chưa đánh giá.

## Dashboard và admin

- Dashboard (chỉ tài khoản owner): bảng "Lệnh ngoài kế hoạch" theo ngày và marker cam ở điểm vào lệnh trên chart.
- `/admin/alerts`, mục **Off-plan**: bật/tắt, `poll_seconds`, `no_stop_grace_seconds`, `revenge_window_minutes`, kênh riêng (trống = kênh chung), bật/tắt gửi tin cho từng loại phân loại. Lưu vào cùng file với cấu hình thông báo, có hiệu lực ngay.

## Lưu trữ

SQLite, bảng `off_plan_trades` (mỗi vị thế một dòng, kể cả `IN_PLAN` để so sánh) và `off_plan_events` (`OPENED`, `ADDED`, `NO_STOP`, `STOPS`, `CLOSED`, `RECLASSIFIED`, `REVIEWED`).
