# Đọc tài khoản thật từ sàn

[← README](../README.md)

Tuỳ chọn. Khi chưa có key sàn, mọi con số về lệnh và PnL đều là **lời khai của bạn** qua `log_trade` / `close_trade`. Bật lên thì server đọc thẳng từ sàn, để nhật ký và báo cáo dựa trên số liệu thật.

Hỗ trợ ba sàn, mỗi sàn một kiểu ký riêng, cùng trả về một dạng dữ liệu chuẩn hoá:

| Sàn | `exchange` | Symbol mặc định | Cần passphrase |
|---|---|---|---|
| Binance USDT-M Futures | `binance` | `BTCUSDT` | không |
| Bybit V5 | `bybit` | `BTCUSDT` | không |
| OKX V5 | `okx` | `BTC-USDT-SWAP` | **có** |

## Tạo API key

Làm đúng ba việc này, theo thứ tự:

1. **Chỉ bật quyền đọc.** Tắt Enable Futures/Spot Trading, tắt Enable Withdrawals. Key kiểu này đặt lệnh không được, rút tiền không được.
2. **Bật IP whitelist**, chỉ điền IP của VPS. Key lọt ra ngoài cũng vô dụng ở chỗ khác.
3. **OKX còn đòi passphrase**: chuỗi bạn tự đặt lúc tạo key, không phải mật khẩu đăng nhập.

`exchanges.py` không có hàm nào đặt lệnh, huỷ lệnh, chuyển tiền hay rút tiền. Nhưng đừng dựa vào đó, hãy dựa vào quyền của key.

## Bật đọc tài khoản

**Cách khuyên dùng:** vào `/admin`, mục **Tài khoản sàn**: chọn sàn, dán key/secret/passphrase, tích *Đọc dữ liệu tài khoản thật*, bấm **Kiểm tra kết nối sàn**, rồi Lưu. Có hiệu lực ngay, không cần khởi động lại, kể cả khi đổi hẳn sang sàn khác.

**Cách nạp sẵn:** đặt biến môi trường cho lần chạy đầu. Chỉ có tác dụng khi `settings.json` chưa có key, và cần `account.enabled: true` trong `config.yaml`:

```bash
BAMCP_EXCHANGE=binance
BAMCP_EXCHANGE_KEY=...
BAMCP_EXCHANGE_SECRET=...
BAMCP_EXCHANGE_PASSPHRASE=       # chỉ OKX
```

Key và secret **không bao giờ** nằm trong `config.yaml`.

Khi cấu hình chưa đúng, server vẫn chạy để bạn còn vào admin sửa:

| Tình huống | Hành vi |
|---|---|
| Chưa có key | Phần đọc sàn tắt, log không có dòng `BAMCP account`. `get_positions`, `get_fills` báo lỗi rõ ràng |
| Đã bật nhưng cấu hình sai | Log có `CANH BAO: cau hinh san chua dung`, sửa ở `/admin` |
| Sai key, passphrase, hoặc IP chưa whitelist | Nút **Kiểm tra kết nối sàn** báo ngay, không phải mò trong log |

## Nó đổi gì trong `get_today_status`

Đây là thay đổi quan trọng nhất. Khi bật account, bước check trước khi vào lệnh không còn tin nhật ký nữa:

- `trades_taken` = `max(số lệnh trong nhật ký, số order trên sàn)`: vào lệnh mà quên log thì vẫn bị tính vào quota.
- `realized_pnl` = **net thật** từ sao kê sàn: lãi/lỗ đã chốt **cộng** phí giao dịch **cộng** funding. Con số này gần như luôn xấu hơn số bạn tự khai, vì log tay hầu như không bao giờ ghi phí và funding.
- `counted_from` cho biết đang đếm theo `exchange` hay `journal`.
- `journal_pnl` vẫn được giữ riêng để bạn thấy khoảng lệch.
- `by_symbol` cho thấy PnL từng cặp, `errors` cho biết cặp nào đọc không được. Số liệu thiếu thì phải nhìn thấy, không được im lặng.

Mất kết nối tới sàn thì `get_today_status` **không lỗi**: nó quay về dùng số trong nhật ký và đặt `exchange.error` để bạn biết con số đang kém tin cậy. Rào chắn kỷ luật không được phép sập vì mạng chập chờn.

## Một vị thế = một lệnh

TP từng phần và SL là những **order riêng** trên sàn, nhưng theo cách hiểu của người giao dịch chúng vẫn thuộc **một lệnh**. Đếm theo `order_id` sẽ thổi phồng số lệnh trong ngày và làm quota hết sớm giả.

Cả ba sàn đều không trả về position id trong lịch sử fill, nên server dựng lại: cộng dồn khối lượng có dấu, về 0 là đóng vị thế. Fill có `realized_pnl` khác 0 là fill đóng bớt, bằng 0 là fill mở hoặc thêm vào.

Ví dụ thật, bốn fill trong một ngày:

```
00:39  sell 7.27   pnl −11.03  ┐ P1  mở từ hôm trước
15:13  sell 2.30   pnl  −3.78  ┘     (carried_in)
15:43  buy  11.25  pnl      0  ┐ P2  mở và đóng trong ngày
16:24  sell 11.25  pnl −13.62  ┘
```

Đây là **2 vị thế**, và chỉ **1** tính vào quota hôm nay. Vị thế mang từ hôm trước sang đã tính vào quota của hôm đó rồi, tính lại là phạt bạn hai lần.

`get_fills` trả về danh sách vị thế, mỗi vị thế có `position_id`, `carried_in`, `closed`, tổng `realized_pnl` và các fill thành phần. `get_today_status` và `reconcile_journal` đều đếm theo vị thế.

Các trường hợp đã kiểm: TP từng phần (mở 10, chốt 3+3+4 → 1 vị thế), scale in (5+5 rồi đóng 10 → 1), hai vị thế độc lập → 2, vị thế còn mở cuối ngày → 1 với `still_open`.

## Đọc theo danh sách cặp

Bốn tool đọc tài khoản (`get_positions`, `get_fills`, `get_account_pnl`, `reconcile_journal`) đều chạy trên **toàn bộ danh sách cặp trong trang admin**. Thêm một cặp ở đó là nó tự động được đọc, không phải khai lại ở đâu. Mỗi tool nhận `symbol` để lọc về một cặp, bỏ trống là gộp tất cả.

**Mã sàn tự suy.** Cặp phân tích là `ETHUSDT`, nhưng mỗi sàn gọi một kiểu:

| Cặp | Binance | Bybit | OKX |
|---|---|---|---|
| `BTCUSDT` | `BTCUSDT` | `BTCUSDT` | `BTC-USDT-SWAP` |
| `ETHUSDT` | `ETHUSDT` | `ETHUSDT` | `ETH-USDT-SWAP` |

Khai sẵn dạng có dấu gạch (`BTC-USDT-SWAP`) thì giữ nguyên, dùng cho mã không suy ra được theo quy tắc.

**Vị thế gom riêng từng cặp.** Khối lượng cộng dồn của BTC và ETH không thể trộn vào một chuỗi, trộn vào là ranh giới vị thế sai hoàn toàn. `position_id` mang tiền tố cặp: `BTCUSDT-P1`.

**Một cặp lỗi không kéo đổ cả lần gọi.** Cặp sàn không niêm yết được ghi vào `errors` rồi đi tiếp. Cặp lỗi còn được nhớ trong 30 phút để không làm chậm mọi lần gọi sau. Ví dụ `XAUUSDT` có trên Binance nhưng có thể không có trên OKX.

## `reconcile_journal`

Chạy cuối ngày, hoặc bất cứ lúc nào nghi mình quên log. Nó chỉ ra ba loại lệch:

- Sàn ghi nhận nhiều order hơn nhật ký → có lệnh chưa log.
- Nhật ký nhiều hơn sàn → có lệnh log nhưng không khớp.
- PnL lệch quá `pnl_tolerance` → thường là phí và funding chưa được tính.

Nó **không tự sửa nhật ký**. Chỉ nói ra chỗ lệch, để bạn quyết định.
