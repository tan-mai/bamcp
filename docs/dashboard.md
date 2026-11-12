# Setup dashboard

[← README](../README.md)

Trang `https://<domain>/setup-dashboard` vẽ bias, setup, kịch bản và TR của từng cặp lên biểu đồ nến, cập nhật theo watcher, kèm chế độ review theo ngày. Trang chỉ đọc: không endpoint nào đặt, sửa, huỷ lệnh hay setup. Ngoại lệ duy nhất là lưu kết quả review.

Deep link, cũng là dạng `dashboard_url` mà tool và tin push trả về: `/setup-dashboard?symbol=ETHUSDT&date=2026-11-07&mode=review&tf=15m`.

## Bắt đầu

1. Tạo tài khoản ở `/admin/dashboard-accounts`. Dashboard có đăng nhập riêng, không dùng chung với admin.
2. Nếu deploy ở domain khác, đổi `dashboard.base_url` để link trong tool và tin báo trỏ đúng trang.

## Quy trình cho Claude

1. `get_chart_data(symbol, "15m", 96+)` đọc TR, pha, event → `update_tr` một lần với mọi thay đổi Wyckoff và `prob_evidence` → `save_bias` → `save_setups` (kèm `branch`, `entry_event`, `event_ref`, `expects`) → `save_scenarios`. Mỗi lần lưu sinh một snapshot bất biến (`plan_snapshots`).
2. Cần vẽ biểu đồ inline thì gọi `get_chart_data`, cùng một nguồn với trang.
3. Cuối ngày: `get_chart_data(date=..., revision="origin")` để so kế hoạch gốc với nến thật, rồi `save_review`.

Kịch bản: `points` có ít nhất 2 điểm, thời gian tăng dần. `t` là epoch ms, `'YYYY-MM-DD HH:MM'` giờ VN, hoặc offset `{bars, tf}` tính từ nến đã đóng cuối lúc lưu. Đổi trạng thái một kịch bản đã có: `save_scenarios(symbol, [{code, status}], replace=false)`.

## TR khoá và Wyckoff

TR khoá là nơi duy nhất lưu cấu trúc Wyckoff: sơ đồ, các pha A–E và từng sự kiện kèm số VSA. Claude gắn nhãn, server chỉ kiểm tra tính hợp lệ và tính sẵn số VSA. Setup chỉ liên kết tới TR ([setups.md](setups.md#liên-kết-tr-khoá)).

**Tạo và vẽ lại TR** qua `save_scenarios(tr={timeframe, tr_high, tr_low, since, redraw_rule, ...})`. Cùng ba neo `tr_high`, `tr_low`, `since` với TR đang hiệu lực thì gộp vào TR đó, gửi lại event đã có (trùng `type` + `t`) không bị nhân đôi. Neo khác thì tạo TR mới, TR cũ `superseded_by` và `status` chuyển `completed` (hoặc `failed` nếu truyền `old_tr_status="failed"`).

**Cập nhật trong ngày** qua `update_tr(symbol, schematic, status, levels, note, phases, add_events, update_events)`. Không đổi được `tr_high`, `tr_low`, `since`. Có lỗi nào thì từ chối cả lần gọi và liệt kê hết lỗi.

| Trường | Giá trị |
|---|---|
| `schematic` | `accumulation`, `reaccumulation`, `distribution`, `redistribution`, `undetermined` (mặc định, nhận mọi type) |
| `status` của TR | `hypothesis` (mặc định), `confirmed`, `failed`, `completed` |
| `levels` | `{creek, ice, ar_level, st_level}`, gộp vào mức đang có, `null` là xoá |
| `phases` | `[{phase, start, end?, note?}]`, upsert theo pha. `start`/`end` nhận epoch ms, `'YYYY-MM-DD HH:MM'` giờ VN, hoặc `{event: "SPRING#1"}`. Mở pha mới thì pha trước tự đóng. Pha không chồng nhau, theo thứ tự A → E |
| `status` của event | `tentative` (mặc định), `confirmed`, `failed`. Gán sai thì `failed` kèm `note`, không xoá |

Danh mục event (side quyết định marker nằm dưới hay trên nến):

| Sơ đồ | Pha | Đáy (`low`) | Đỉnh (`high`) |
|---|---|---|---|
| Tích luỹ | A | `PS`, `SC`, `ST` | `AR` |
| Tích luỹ | B | `ST_B`, `mSOW` | `UA` |
| Tích luỹ | C | `SPRING`, `SHAKEOUT`, `TEST` | |
| Tích luỹ | D | `LPS`, `BU` | `SOS`, `JAC` |
| Phân phối | A | `AR_D` | `PSY`, `BC`, `ST_D` |
| Phân phối | B | | `UT`, `mSOS` |
| Phân phối | C | | `UTAD`, `TEST_D` |
| Phân phối | D | `SOW` | `LPSY` |

Server từ chối event khi: `t` không phải giờ mở của một nến đã đóng của `tf`; `t` trước `since` quá 3 nến (trừ `PS`/`PSY`); `price` ngoài [low, high] của nến đó; type không khớp `schematic`; trùng `type` + `t`. Chỉ cảnh báo khi: type nằm ngoài pha thường gặp; `price` lệch đáy/đỉnh nến quá 0.1%; `SOS`/`SOW` `confirmed` mà chưa có `LPS`/`LPSY` sau nó.

Số VSA tính trên nến của event: `vol_ratio` = vol / SMA20 của 20 nến trước, `spread_ratio` = (high − low) / spread TB 20 nến trước, `close_pct` = vị trí đóng cửa trong nến (0–100). `seq` là thứ tự theo thời gian trong TR. Tham chiếu `TEST#2` là TEST thứ hai, nhãn trên biểu đồ là `TEST 2`.

`delete_tr_event(symbol, event_id, reason)` chỉ xoá được event tạo trong 24h, lưu lại bản đã xoá kèm lý do vào `tr_event_deletions`.

**Trên biểu đồ**

- Dải pha: nền dọc xen kẽ hai mức xám, nhãn `A`…`E` ở mép trên, pha đang chạy kéo tới nến cuối.
- Marker: event đáy đặt dưới nến (mũi tên lên), event đỉnh đặt trên nến. Màu theo sơ đồ: tích luỹ xanh, phân phối đỏ, chưa xác định xám. `confirmed` màu đậm, `tentative` nhạt kèm `?`, `failed` xám gạch ngang và ẩn mặc định (bật "Hiện event hỏng"). Ở H1/H4, event M15 rơi vào nến chứa nó; nhiều marker cùng nến thì xếp chồng.
- Creek, ice: nét đứt có nhãn, bắt đầu từ `since`.
- Bấm hoặc chạm marker: popover có thời gian, giá, `vol_ratio`, `spread_ratio`, `close_pct`, trạng thái, `note`, event xác nhận.
- Bật / tắt lớp Pha, Event, Creek/Ice, nhớ theo trình duyệt.

**Thẻ TR** nằm trên thẻ setup: sơ đồ và trạng thái, pha hiện tại đã kéo dài bao lâu, TR high/low/since, creek/ice, danh sách event theo pha (bấm một dòng thì biểu đồ cuộn tới nến đó), `redraw_rule` và `note`. Ở Review, chọn revision nào thì TR, pha, event vẽ theo snapshot đó; nút "Thay đổi TR trong ngày" liệt kê event thêm và đổi trạng thái giữa revision 1 và revision cuối.

**Chuyển dữ liệu cũ.** Lúc khởi động, event trong cột json `events` cũ của mỗi TR được chuyển thành dòng `tr_events` (`tentative`, `created_by = claude`, tính lại `seq`, `side`, số VSA từ nến). Cột json cũ giữ lại tới khi nghiệm thu xong. Snapshot cũ giữ nguyên và vẫn đọc được.

## Nhánh kịch bản và xác suất

TR khoá M15 trung tính (`branch = null`) cho tới khi có sự kiện pha C, hoặc pha D nếu không có pha C. Nhánh không lưu riêng: server suy ra mỗi lần đọc TR, nên snapshot cũ cũng tính lại được.

| Nguồn (theo thứ tự ưu tiên) | Nhánh tăng (`accumulation`) | Nhánh giảm (`distribution`) |
|---|---|---|
| Event pha C `confirmed` (lấy event muộn nhất) | `SPRING`, `SHAKEOUT` | `UTAD` |
| Không có pha C: event pha D `confirmed` | `SOS`, `JAC` | `SOW` |
| Không có event nào: `schematic` | `accumulation`, `reaccumulation` | `distribution`, `redistribution` |

Đổi `schematic` sang một họ khi chưa có event pha C/D `confirmed` cùng nhánh thì vẫn xác định nhánh, kèm cảnh báo.

**Loại setup ngược nhánh.** Mỗi lần `update_tr` hoặc `save_scenarios` ghi TR, nếu nhánh vừa được xác định hoặc đổi:

- Mọi setup **chưa kết thúc** của TR đó có hướng ngược nhánh (kể cả `branch=range`) chuyển `PRUNED`, trạng thái kết thúc, watcher ngừng theo dõi. Event `PRUNED` của setup ghi `reason`, vd `TR xác định nhánh giảm bởi UTAD#1 (pha C)`.
- Setup đã `FILLED` mà review chưa ghi lệnh đã đóng thì giữ nguyên, chỉ ghi event `BRANCH_CONFLICT` và cảnh báo "vị thế ngược nhánh TR".
- Gửi **một** tin tổng hợp (event hệ thống `TR_BRANCH`) liệt kê label bị loại và vị thế ngược nhánh.
- Kết quả tool có khối `branch` gồm `reason`, `pruned`, `conflicts`. Snapshot mới cho hôm nay và cho mọi ngày có setup vừa bị loại.
- Event pha C/D sau đó chuyển `failed`: không tự khôi phục setup đã `PRUNED`, chỉ trả `warnings` để Claude lưu lại nếu cần.
- Sau khi TR có nhánh, `save_setups` từ chối setup ngược nhánh.

**Xác suất tích luỹ / phân phối.** `update_tr(prob_evidence=[{factor, side: acc|dist, weight?, note?}])` thay toàn bộ danh sách mỗi lần gửi (`[]` là xoá). `prob_accumulation` = Σ trọng số `acc` / Σ trọng số × 100 (làm tròn nửa lên), `prob_distribution` = 100 − `prob_accumulation`. Mỗi lần đổi ghi một dòng `tr_prob_history` gắn plan revision. Đây là điểm bằng chứng, chưa hiệu chỉnh thống kê.

| `factor` | Yếu tố | Trọng số mặc định |
|---|---|---|
| `htf_trend` | Xu hướng D/H4 trước TR (cùng hướng) | 2 |
| `phase_b_lows` | Cấu trúc đáy pha B (cao dần → acc / thấp dần → dist) | 1 |
| `phase_b_highs` | Cấu trúc đỉnh pha B (cao dần → acc / thấp dần → dist) | 1 |
| `test_highs` | Hành vi test đỉnh (cung → dist, hấp thụ → acc) | 1 |
| `test_lows` | Hành vi test đáy (hấp thụ → acc, không có cầu → dist) | 1 |
| `phase_cd_event` | Sự kiện pha C/D đã confirmed (SPRING/SOS → acc; UTAD/SOW → dist) | 3 |

Bỏ trống `weight` thì lấy trọng số đang đặt ở `/admin/dashboard-accounts` (card "Thang điểm xác suất TR"). Factor ngoài bảng phải khai `weight`. Chỉ cảnh báo, không từ chối: `weight` khác thang điểm, factor ngoài bảng, factor khai hai lần, `phase_cd_event` mà TR chưa có event C/D `confirmed` cùng bên.

**Trên dashboard**

- Thẻ TR có banner nhánh, vd `Pha C: UTAD#1 → Nhánh giảm (phân phối)`, thanh % tích luỹ / phân phối, danh sách bằng chứng (yếu tố, bên, trọng số, note) và diễn biến xác suất trong ngày.
- Setup `PRUNED` ẩn mặc định khỏi danh sách và biểu đồ. Nút "Hiện setup đã loại (n)" hiện lại, thẻ setup ghi lý do loại.
- Thẻ setup dùng `label` làm tiêu đề, mã nằm ở dòng dưới.

## Tài khoản và quyền

Tài khoản nằm trong bảng `dashboard_users`. Mật khẩu hash argon2, không dùng chung với admin: phiên admin không vào được dashboard và ngược lại.

| Quyền | `owner` | `viewer` |
|---|---|---|
| Xem Live, Review, tab Thống kê | có | có |
| Lưu kết quả review | có | không (API 403) |
| Vị thế mở, PnL, lệnh OKX | có | không (API 403, `/api/chart` không có `positions`, timeline bỏ `UNPLANNED_POSITION` và giá khớp) |
| Ghi chú: `note` của setup, ghi chú review, `note` của điểm kịch bản | có | không (API không trả) |
| TR khoá: pha, event, thẻ TR, kể cả `note` của TR, pha, event | có | có |

Viewer vẫn đọc được `summary`, `action_zone` của bias, `label`, `condition` của kịch bản và toàn bộ TR, nên không ghi số vị thế hay PnL vào các chỗ đó. `notes` của `save_bias` không gửi xuống trình duyệt với mọi tài khoản.

### Mất quyền vào admin

Tạo lại owner bằng lệnh dự phòng:

```bash
docker exec -it bamcp bamcp dashboard create-owner <username>   # ngoài Docker: python bamcp_cli.py dashboard ...
```

Username đã có thì lệnh khôi phục thành owner đang hoạt động, đặt lại mật khẩu và đăng xuất mọi phiên của tài khoản đó.

### Phiên đăng nhập

- Cookie `HttpOnly; Secure; SameSite=Lax`, hết hạn trượt `session_hours`, tick "Ghi nhớ" thì `remember_days`. DB chỉ lưu SHA-256 của token.
- Sai liên tiếp `login_max_attempts` lần theo IP thì khoá `login_lockout_minutes` phút. IP lấy từ `X-Forwarded-For` do proxy đặt (`server.behind_proxy`), nên proxy phải ghi đè header này từ client. Traefik và Caddy mặc định đã làm vậy.
- Cookie `Secure` nghĩa là chạy thử chỉ đăng nhập được qua HTTPS hoặc `http://localhost`, không qua IP LAN.

## Review

Mặc định Review vẽ **kế hoạch gốc**: mỗi phần (bias, setup, kịch bản, TR) lấy bản đầu tiên có nội dung trong ngày. Lý do: quy trình chuẩn là `save_bias` trước, nên revision 1 thường chỉ có bias. Dropdown vẫn chọn được đúng từng revision, kể cả R1. Nến luôn là thật, vẽ tới hết ngày bias + 24h.

**`auto_outcome`** được tính lần đầu khi đọc setup đã kết thúc, từ nến M5 sau `TRIGGERED` đầu tiên qua được confirm: chạm TP trước là `tp_hit`, chạm SL trước hoặc cùng nến là `sl_hit`. Setup `FILLED` chưa chạm mốc nào thì để trống và tính lại lần sau. Kho không còn nến từ lúc trigger thì không kết luận.

**`save_review`** ghi đè `outcome`, không đụng `auto_outcome`. Khi lưu, `tp_hit` chuyển kịch bản active gắn với setup sang `played_out`; `sl_hit`, `invalidated` sang `failed`.

**Thống kê.** Win rate = thắng / lệnh đã đóng (`tp_hit`, `sl_hit`, `be`, `manual_exit`). Thắng là `tp_hit`, hoặc `be` / `manual_exit` có R dương. R trung bình chỉ tính setup có `r_multiple`. Nhóm theo `phase` (pha lúc tạo setup), `event_ref_type`, `expects`, `schematic` (của TR liên kết), `branch`, `entry_event`, hoặc nhóm chéo `direction,event_ref_type,phase`. Setup không có giá trị nằm ở nhóm `chua_co`.

## Cấu hình

Khối `dashboard` trong `config.yaml` là giá trị khởi tạo. Sửa ở `/admin/dashboard-accounts` thì ghi vào `dashboard/config.json` và có hiệu lực ngay.

- `enabled: false`: mọi route trả 404 và tool không trả link.
- `hide_pnl: true`: số PnL không xuống trình duyệt, kể cả owner.
- `prob_weights`: thang điểm xác suất TR ([chi tiết](#nhánh-kịch-bản-và-xác-suất)).

Nến đang chạy: kho SQLite không lưu nến chưa đóng nên server ghép từ các nến M5 đã đóng, trễ tối đa một nến M5. Khung 5M vì thế không có nến đang chạy.

Biểu đồ dùng TradingView Lightweight Charts 4.2.3 (Apache 2.0), tự host trong `static/vendor/`.

## API của trang

Tất cả dưới `/setup-dashboard/api`, cần phiên, trả JSON, dùng chung hàm với tool MCP. `POST` cần header `X-CSRF-Token`.

| Method + path | Dùng cho | Quyền |
|---|---|---|
| `GET /symbols` | Danh sách cặp | mọi tài khoản |
| `GET /chart?symbol&tf&bars&date&revision&include_forming` | Toàn bộ dữ liệu biểu đồ, cache tới khi có nến mới hoặc kế hoạch đổi | mọi tài khoản |
| `GET /setups?symbol&date` | Thẻ setup và review | mọi tài khoản |
| `GET /events?symbol&date` | Timeline | mọi tài khoản |
| `GET /snapshots?symbol&date` | Danh sách revision | mọi tài khoản |
| `GET /review-stats?symbol&from&to&group_by` | Tab Thống kê | mọi tài khoản |
| `GET /tr-changes?symbol&date` | Event thêm / đổi trạng thái giữa revision 1 và revision cuối của ngày | mọi tài khoản |
| `GET /positions?symbol` | Vị thế mở, gọi sàn cache 30 giây | owner |
| `GET /fills?symbol&date` | Lệnh OKX trong ngày, `setup_code` rỗng là lệnh không gắn setup | owner |
| `POST /reviews` | Lưu review `{setup_code, outcome, r_multiple, note}` | owner |
