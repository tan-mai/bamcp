# Phân tích GANN

[← README](../README.md)

Module GANN có hai phần: **thời gian** (pivot, trạng thái sóng, cửa sổ thời gian) và **giá** (mốc Square of 9). Dữ liệu đọc từ nến đã có trong `data_root`, module này không tự gọi Binance.

> **Đây là giả thuyết, chưa được kiểm chứng.** Mọi trọng số trong `config.yaml` là số khởi điểm. Backtest BTCUSDT 2020-2026 chưa thấy hiệu ứng của cửa sổ thời gian (lift `range_atr` 0.92, p 0.95), nên chúng không được đưa vào `get_context` theo mặc định. Không dùng riêng một cửa sổ hay một mốc giá để đề xuất vào lệnh.

## Bật cho một cặp

Bật/tắt từng cặp ở `/admin/gann`, mục **Cặp phân tích Gann**. Có hiệu lực ngay, không cần restart. Cặp tắt thì tool GANN từ chối và chỉ cho bạn biết bật ở đâu.

- `time_windows.symbols` trong `config.yaml` chỉ là mặc định cho lần đầu, trước khi bấm ô nào trên trang đó. Danh sách rỗng nghĩa là không cặp nào.
- Cặp phải đang được [theo dõi](data.md#theo-dõi-nhiều-cặp) thì mới có dữ liệu nến.
- Khối `retention` phải để `1d` và `1w` là `null` thì pivot mới có đủ lịch sử. Vừa `backfill_klines` thêm lịch sử cũ thì gọi `recompute_pivots`.
- `time_windows.enabled: false` tắt cả module và trang `/admin/gann`.

## Tool

| Tool | Dùng khi | Ghi chú |
|---|---|---|
| `get_pivots` | Cần đỉnh/đáy đã xác nhận | `timeframe`: `1w` hoặc `1d`. `level`: `major`, `intermediate`, `minor` |
| `get_swing_state` | Hỏi xu hướng và nhịp hồi có bất thường không | Đọc `note` trước, nó gói kết luận trong một câu |
| `get_time_windows` | Hỏi ngày nào đáng chú ý | `as_of` tính như đang đứng ở cuối ngày đó, chỉ dùng pivot lúc đó đã biết |
| `backtest_time_windows` | Kiểm chứng cửa sổ thời gian | Kết quả có `suggested_weights` nhưng không tự ghi vào config |
| `recompute_pivots` | Vừa backfill, hoặc muốn chắc chắn | Bình thường không cần: cache tự hết hiệu lực khi có nến mới đóng, khi đổi config hay đổi pivot thủ công |
| `get_sq9_levels` | Hỏi mốc giá quanh giá hiện tại | `anchors`: `auto` hoặc danh sách giá cách nhau bằng dấu phẩy |

## Pivot và trạng thái sóng

Pivot ở đây **không phải** swing fractal của `get_context`. Một pivot chỉ được tính sau khi có đủ nến đảo chiều, nên `confirmed_at` luôn muộn hơn `time`. Đó là giá trị của nó: đây là thứ lúc đó thực sự đã biết.

`get_swing_state` báo overbalance, dấu hiệu Gann coi là xu hướng đổi chứ không còn là nhịp hồi bình thường:

- `time_overbalanced`: nhịp hồi hiện tại **dài** hơn mọi nhịp hồi trước trong cùng xu hướng.
- `price_overbalanced`: nhịp hồi hiện tại **sâu** hơn mọi nhịp trước.

Đang chạy cùng chiều xu hướng thì cả hai là `false`.

## Cửa sổ thời gian

Từ mỗi pivot đã xác nhận, hệ thống đếm ra các mốc. Ngày nào nhiều phép đếm cùng rơi vào thì điểm cao. Dưới `scoring.min_score` thì không gọi là cửa sổ.

| Phép đếm | Ý nghĩa |
|---|---|
| `cycle` | Chu kỳ theo số ngày (30, 45, 60, 90, 120...) |
| `anniversary` | Kỷ niệm năm của pivot |
| `seasonal` | Mốc theo mùa |
| `swing_duration` | Độ dài chân sóng lặp lại |
| `event` | Sự kiện vĩ mô nhập tay |
| `range_square` | Mặc định tắt: `scale_factor` phụ thuộc thang đo giá của từng cặp, phải đo bằng backtest trước |

Cửa sổ chỉ nói về thời điểm, không nói giá sẽ đi hướng nào.

### Backtest

`backtest_time_windows` chạy walk-forward, không lookahead: với mỗi ngày, nó tính điểm như đang đứng ở cuối ngày trước đó, rồi so ngày trong cửa sổ với ngày ngoài cửa sổ trên 3 chỉ số: `range_atr`, `volume_z`, `pivot_near`. P-value lấy từ permutation test.

Chạy quá ~55 giây thì tool trả `status: running` kèm `run_id`, gọi `get_backtest_result(run_id)` sau. `exclude_events=True` tắt chiều sự kiện để đo riêng phần Gann.

## Mốc giá Square of 9

Mỗi anchor A sinh mức L = (√A + θ/180)², θ theo bước `step_deg`. Mức gần nhau gộp thành zone. Điểm zone = trọng số bậc anchor × trọng số góc. Mỗi zone kèm số lần giá đã chạm, đã bật lại (`reactions`), đã phá (`breaks`).

Đọc kết quả:

- `note`: đọc trước tiên.
- `nearest_above`, `nearest_below`: zone gần giá nhất mỗi phía.
- `strongest_above`, `strongest_below`: zone điểm cao nhất mỗi phía.
- Warning "anchor điều hoà": hai anchor sinh cùng một dãy mức, nên zone trùng nhau do cấu trúc chứ không phải hội tụ độc lập.

Khối `gann_price_levels` trong `config.yaml` có sẵn phần `fan`, `confluence` và `backtest`, nhưng hiện chỉ `get_sq9_levels` được mở thành tool. Chưa có tool backtest cho mốc giá.

## Trong `get_context`

Với cặp đã bật GANN, `get_context` có thêm tối đa hai trường:

| Trường | Mặc định | Tắt bằng |
|---|---|---|
| `swing_state` | Bật | `time_windows.context.include_in_get_context: false` (tắt cả hai) |
| `time_windows_next_7d` | **Tắt**, vì backtest chưa thấy hiệu ứng | Bật bằng `time_windows.context.time_windows: true` |

## Trang `/admin/gann`

- **Cặp phân tích Gann**: bật/tắt từng cặp.
- **Pivot theo cặp**: xem pivot đã tính.
- **Pivot thủ công**: nhập pivot tay.
- **Sự kiện vĩ mô**: nhập sự kiện cho phép đếm `event`.
- **Backtest gần nhất**: kết quả chạy gần nhất.
- **Config**: tham số của module.

## Dữ liệu

Nằm trong `<data_root>/time_windows/`:

| Đường dẫn | Có tính lại được không |
|---|---|
| `pivots/<SYMBOL>.json` | Có, xoá đi thì `recompute_pivots` tính lại |
| `backtests/tw-<thời điểm>-<mã>/` | Có, chạy lại backtest |
| `manual_pivots.json` | **Không**, đây là dữ liệu gốc bạn nhập |
| `events.json` | **Không**, đây là dữ liệu gốc bạn nhập |
