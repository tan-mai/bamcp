# Quy định giao dịch

[← README](../README.md)

Quy định chia làm hai loại, sửa bằng hai đường khác nhau:

- **Rule bằng số** nằm trong `data/rules.json`, được đọc mới **mỗi lần gọi tool**, không cache. Đổi là có hiệu lực ngay, không cần restart.
- **Quy trình phân tích** nằm trong `config.yaml`. Xem [cuối trang](#quy-trình-phân-tích).

Lệnh ORB dùng [bộ rule riêng](orb.md#bộ-rule-orb-riêng): không chấm bằng các rule dưới đây (trừ `max_margin_per_trade`) và không ăn quota/daily stop chung.

## Hai phạm vi

**Chung cho cả tài khoản.** Chỉ một bộ, hết quota là hết dù lệnh nằm ở cặp nào:

| Rule | Mặc định |
|---|---|
| `max_trades_per_day` | 3 |
| `max_margin_per_trade` | 20 |
| `daily_stop_loss` | -20 |
| `swing_max_margin_per_trade` | 0 *(0 = không giới hạn)* |

Ngân sách rủi ro dùng chung là cố ý: nếu mỗi cặp một bộ quota thì thêm 5 cặp là nhân ngân sách rủi ro lên 5 lần, mà tài khoản thì vẫn chỉ có một.

**Riêng từng cặp.** Mỗi cặp giữ ngưỡng của nó, cặp nào chưa đặt riêng thì kế thừa đúng những số mặc định này:

| Rule | Mặc định |
|---|---|
| `max_stop_points` | 300 |
| `min_take_profit_points` | 500 |
| `swing_min_take_profit_points` | 2000 |
| `swing_max_stop_points` | 1500 |
| `min_rr` | 1.5 *(R:R tối thiểu, không đo bằng điểm)* |

Ngưỡng đo bằng **điểm giá** phải riêng: 300 điểm trên BTC là một nhịp nhỏ, 300 điểm trên ADA là cả một chu kỳ. Ép chung một con số thì hoặc quá chặt cho cặp này, hoặc quá lỏng cho cặp kia.

## `min_rr`

R:R = khoảng cách tới TP ÷ khoảng cách tới SL, tính từ giá vào. Lệnh đạt khi R:R ≥ `min_rr` của cặp. Áp cho mọi lệnh không phải ORB, cả scalp lẫn swing.

- R:R thấp hơn thì `check_trade` trả `would_pass: false` kèm vi phạm `R:R 1.25 < min_rr 1.5`, và `log_trade` chạy đúng kiểm tra đó.
- `get_rules(symbol=...)` trả khối `min_rr` với `value` và `source`: `symbol` nếu cặp đặt riêng, `default` nếu đang kế thừa.
- Đổi cho một cặp: `update_rules({"min_rr": 2}, reason="...", symbol="ETHUSDT")`. Bỏ trống `symbol` là đổi mặc định cho mọi cặp chưa đặt riêng.

## Scalp và swing

Mặc định mọi lệnh là scalp và theo hạn mức `max_margin_per_trade`. Khai `trade_type="swing"` mà TP không đạt `swing_min_take_profit_points` thì hệ thống tự hạ xuống scalp và ghi lại. Gọi `check_trade` để biết lệnh được xếp loại gì trước khi chốt size.

## Đổi rule

### Trong chat

> *"Từ hôm nay giảm xuống 2 lệnh một ngày, tôi đang vào lệnh quá tay."*

> *"ADA điểm nhỏ quá, SL tối đa của riêng ADAUSDT để 15 điểm thôi."*

Claude gọi `update_rules({"max_trades_per_day": 2}, reason="...")` cho rule chung, và `update_rules({"max_stop_points": 15}, reason="...", symbol="ADAUSDT")` cho rule riêng. `reason` là tham số bắt buộc ở cả hai, không có lý do thì tool báo lỗi.

**Không nhầm phạm vi được.** Gửi một rule chung kèm `symbol` thì tool từ chối chứ không âm thầm ghi nó vào một cặp. Nếu không, bạn tưởng mình siết quota cả tài khoản trong khi thật ra chỉ siết mỗi ADA. Chiều ngược lại, `get_rules(symbol="ETHUSDT")` trả về bộ số đã giải cho ETH, kèm khối `symbol_overrides` cho biết cặp nào đang đặt riêng những rule gì.

### Trên trang admin

Trang admin sửa được cả hai phạm vi và **đi qua đúng một cửa với Claude**: cùng hàm `_apply_rule_changes`, cùng validate, cùng ghi vào `rules.json`.

- Thẻ **Quy định chung**: 4 rule áp cho mọi cặp.
- Thẻ **Quy định theo từng cặp**: chọn cặp trong ô *Đang sửa cặp* rồi nhập. Dòng chú thích ngay dưới cho biết cặp đó đang đặt riêng những rule gì, hay đang kế thừa toàn bộ mặc định. Sửa nhiều cặp rồi bấm Lưu một lần cũng được: những gì bạn nhập cho từng cặp được giữ lại khi đổi lựa chọn.
- Thẻ **Lý do & lịch sử**: một ô lý do dùng chung cho cả lần lưu, và 5 lần đổi gần nhất kèm phạm vi (`[chung]` hay `[ADAUSDT]`), giá trị cũ → mới, lý do.

Nghĩa là **bắt buộc có lý do** dù bạn đổi ở đâu, phạm vi nào. Trang admin không phải cửa sau vòng qua cơ chế ghi dấu, nếu không thì toàn bộ `rules_changed_today` và `rules_at_entry` mất giá trị.

Chỉ những ô **thực sự đổi** mới được gửi lên, nên bấm Lưu mà không động vào rule thì không sinh bản ghi lịch sử rỗng.

### Server chặn gì

Server chỉ chặn giá trị làm vỡ logic tính toán. Ngoài ra nới hay siết bao nhiêu là quyền bạn.

- `daily_stop_loss` phải `<= 0`, `min_rr` phải `> 0`, các rule còn lại phải `>= 0`.
- Tên rule phải có thật. Gõ sai tên là báo lỗi chứ không âm thầm tạo rule mới.
- Cặp phải đang được theo dõi. Gõ sai tên cặp thì không tạo ra một bộ rule mồ côi.

## Mọi thay đổi đều để lại dấu

`rules.json` giữ `history` đầy đủ: đổi gì, từ bao nhiêu sang bao nhiêu, lúc nào, vì sao, và **cho cặp nào**. "Siết cả tài khoản" với "siết mỗi ADA" là hai dòng khác nhau trong lịch sử.

- `get_today_status` trả `rules_changed_today`: rule bị đổi trong chính ngày đang giao dịch thì hiện ra ngay ở bước check trước khi vào lệnh.
- `log_trade` ghi `rules_at_entry` vào từng lệnh: đọc lại nhật ký cũ vẫn biết lúc đó mình chơi theo luật nào.

Đổi rule giữa lúc đang lỗ thì vẫn đổi được. Nhưng bản ghi sẽ nói ra điều đó, và Claude được dặn phải nhắc lại trước khi bàn tiếp chuyện vào lệnh.

## Nguồn sự thật

Khối `rules:` trong `config.yaml` chỉ là **giá trị khởi tạo**. Lần đầu `update_rules` chạy, bản sao được ghi sang `data/rules.json` và từ đó file đó là nguồn sự thật. Sửa `config.yaml` về sau chỉ có tác dụng khi bạn thêm một rule hoàn toàn mới.

Bỏ một cặp khỏi danh sách theo dõi **không** xoá phần rule đặt riêng của nó trong `rules.json`, giống như file nến cũ vẫn được giữ lại. Thêm cặp đó trở lại thì ngưỡng riêng cũ có hiệu lực lại luôn.

`rules.json` từ bản một-phạm-vi vẫn đọc được nguyên vẹn: nó chỉ có khối `values`, và mọi cặp kế thừa đúng những con số đang có, không cần bước chuyển đổi nào.

## Quy trình phân tích

Nằm ở key `instructions` trong `config.yaml`, không hardcode trong `server.py`. Claude đọc nó một lần lúc bắt tay, nên sửa xong cần `docker compose restart` rồi tắt/bật lại connector trong chat. Không phải build lại image.
