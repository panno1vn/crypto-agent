---
name: nhat-ky
description: Ghi nhật ký kỹ thuật chi tiết theo từng task, mỗi task một file trong docs/nhat-ky/ của repo crypto-agent. Dùng NGAY sau mỗi lần thêm/sửa/xóa file trong repo (code, test, config, docker, dependency, docs có tác động), mỗi khi tìm ra hoặc sửa xong một bug, mỗi quyết định kỹ thuật, và khi hook nhat_ky_guard báo còn file thay đổi chưa có trong nhật ký, kể cả khi Pan không nhắc. Cũng dùng khi Pan nói "ghi nhật ký", "nhật ký", "log lại", "ghi lại thay đổi", "phân tích bug", "post-mortem", "vì sao chọn cách này", "so sánh phương án", "trade-off". Nhật ký phải phân tích nguyên nhân, hệ quả, tác dụng, vì sao chọn, phương án thay thế, khi nào dùng cái nào, trade-off, bằng chứng verify và bài học.
---

# Nhật ký kỹ thuật theo task

Pan dùng nhật ký này để **hiểu** hệ thống (mục tiêu: lên Junior AI Engineer) và làm **bằng chứng** cho portfolio. Một nhật ký tốt không kể lại "đã làm gì". Nó trả lời: *vì sao làm thế, còn cách nào khác, khi nào nên chọn cách khác, đã đánh đổi gì, và lấy gì chứng minh là đúng*. Lịch sử dự án cho thấy kiến thức nằm trong chat thì mất, nằm trong một file dài chung thì không tìm lại được. Vì vậy **mỗi task một file**, đủ để người đọc sau 3 tháng hiểu mà không phải hỏi lại.

## Mỗi task một file

- **Task** là một mục tiêu nhất quán: sửa một bug, thêm một module, cấu hình một thứ, ra một quyết định. Không phải mỗi lần sửa một file.
- **Task tiếp diễn** (lượt sau, phiên sau): cập nhật đúng file cũ, bổ sung các mục và thêm một dòng vào "Tiến trình". Không tạo file mới cho cùng một task.
- **Phát hiện bug trong lúc làm task khác**: bug có file riêng (loại `bug`), hai file trỏ chéo nhau ở mục "Liên kết". Bug có chuỗi phân tích riêng (triệu chứng, nguyên nhân gốc, chống tái phạm) mà nhật ký của task chính không nên ôm.
- **Sửa vặt thuộc task đang làm** (typo, format): ghi vào file của task đó, mục "Thay đổi cụ thể".
- **Nhiều task không liên quan trong một phiên**: nhiều file.

Tên file: `docs/nhat-ky/YYYY-MM-DD_<loai>_<slug>.md`, với `loai` là `thay-doi` hoặc `bug`, `slug` là ASCII không dấu. Trước khi tạo, chạy `ls docs/nhat-ky/` xem task đã có file chưa. Luôn tạo bằng script: nó điền ngày giờ, nhánh, commit gốc và không bao giờ ghi đè file có sẵn.

```bash
python3 .claude/skills/nhat-ky/scripts/tao_nhat_ky.py --loai thay-doi --tieu-de "Tiêu đề task"
python3 .claude/skills/nhat-ky/scripts/tao_nhat_ky.py --loai bug --tieu-de "Mô tả ngắn của bug"
```

## Quy trình

1. **Xác định task và loại.** Đang tiếp tục task nào? Đây là thay đổi hay bug?
2. **Tạo hoặc mở file** (script ở trên, hoặc file cũ nếu task tiếp diễn).
3. **Thu thập dữ kiện thật, không viết theo trí nhớ:** `git status --porcelain`, `git diff --stat`, lệnh đã chạy và output nguyên văn trong cuộc hội thoại, kết quả test. Đọc lại source khi cần mô tả cơ chế.
4. **Điền từng mục** theo template: `templates/thay-doi.md` hoặc `templates/bug.md` (đọc template để biết mỗi mục hỏi gì). Xóa dòng chú thích `<!-- -->` khi đã điền. Mục thật sự không áp dụng thì ghi "Không áp dụng: <lý do>", không bỏ trống.
5. **Nêu đúng đường dẫn mọi file đã thêm/sửa/xóa** trong mục "Thay đổi cụ thể" / "Cách sửa". Hook kiểm tra điều này (xem cuối file).
6. **Tự rà soát** theo checklist bên dưới.
7. **Cập nhật frontmatter:** `trang_thai` (`dang-lam` / `xong` / `treo`), `muc_do_chac_chan` (`da-xac-minh` / `co-dau-hieu` / `chua-verify`).
8. **Báo Pan** đường dẫn file và 2-3 dòng về điều quan trọng nhất, nhất là rủi ro còn lại và việc còn mở.

## Phân tích phải sâu tới đâu

Đây là phần làm nên giá trị của nhật ký. Mỗi mục trả lời một câu hỏi cụ thể:

| Mục | Câu hỏi phải trả lời | Dấu hiệu viết hời hợt |
|---|---|---|
| Nguyên nhân | Vì sao cần thay đổi, hay vì sao bug xảy ra? Nguyên nhân trực tiếp là gì, gốc là gì? Vì sao trước đây không ai thấy? | Chỉ tả triệu chứng; "do code sai" |
| Hệ quả | Đã/sẽ ảnh hưởng gì, tới đâu, bao nhiêu? Cái gì phụ thuộc vào thay đổi này? Hoàn tác thế nào? | Không có con số, không nêu downstream |
| Tác dụng | Giải quyết được gì, đo bằng gì? | "Giúp hệ thống tốt hơn" |
| Vì sao chọn cách này | Cơ chế bên dưới là gì, vì sao cơ chế đó khớp với vấn đề? | Lặp lại tên công cụ thay cho giải thích |
| Phương án thay thế | Ít nhất 2 phương án **thật** (kể cả "không làm gì" nếu hợp lý), mỗi cái có ưu, nhược, vì sao không chọn *lúc này* | Phương án bù nhìn dựng lên để bác bỏ |
| Khi nào dùng cái nào | Điều kiện cụ thể (quy mô dữ liệu, môi trường, mức rủi ro, giai đoạn roadmap) dẫn tới lựa chọn nào | "Tùy trường hợp" |
| Trade-off | Được gì, mất gì: độ phức tạp, hiệu năng, chi phí, bảo trì, bảo mật, khả năng học | Chỉ có ưu điểm |
| Rủi ro còn lại | Cách này KHÔNG giải quyết được gì? Khi nào nó vẫn hỏng? | Không có mục này |
| Bằng chứng | Lệnh + output nguyên văn, trước/sau | "Đã test, chạy ổn" |
| Chống tái phạm | Test/preflight/rule nào bắt được nếu hỏng lại? | "Bài học: cẩn thận hơn" |
| Bài học | Khái niệm nền nào Pan cần hiểu? Liên hệ bug/quyết định cũ nào trong `docs/context/05`, `07`? | Câu chung chung, dự án nào cũng đúng |

Ví dụ mục "Khi nào dùng cái nào". Viết tệ: "Dùng ask rule cho an toàn, sandbox thì an toàn hơn." Viết tốt:

| Tình huống | Chọn | Lý do |
|---|---|---|
| Code/test thường ngày trên laptop, chưa có key giao dịch | auto mode + rule `ask`/`deny` | Chặn đúng nhóm lệnh không hồi phục được, không làm phiền phần còn lại |
| Phiên đụng `.env`, file session, hạ tầng docker | Manual mode | Rule chỉ khớp theo chữ của lệnh; người duyệt từng bước bắt được biến thể |
| Từ N43, có Binance API key | sandbox + `credentials` deny | Chỉ sandbox chặn được script Python tự đọc secret |

## Bằng chứng và trung thực

Luật của repo (CLAUDE.md) áp nguyên vào nhật ký:

- Chỉ viết "đã sửa", "pass", "chạy được" khi output nguyên văn nằm ngay trong nhật ký. Chưa verify thì ghi **CHƯA VERIFY**, kèm lệnh cần chạy và chạy ở đâu (local hay cloud).
- Con số phải có nguồn: lệnh đo, MLflow run, hoặc file GHI_CHU. Không làm tròn cho đẹp.
- Nói đúng mức chắc chắn: "đã xác nhận" khác "có dấu hiệu". Ghi vào `muc_do_chac_chan`.
- Ghi cả hướng điều tra sai, giả định sai, lỗi của chính Claude. Đó là phần Pan học được nhiều nhất.
- Không chép giá trị secret (token, mật khẩu, nội dung `.env`, file session). Cần thì ghi tên biến hoặc độ dài (`${#VAR}`).

## Viết để Pan học được

- Giải thích **cơ chế**, không chỉ thao tác. Ví dụ: vì sao `upsert()` của Chroma merge metadata chứ không replace, thay vì chỉ ghi "đổi add thành upsert".
- Dùng chính code, bug, số liệu của repo làm ví dụ, trỏ tới file và dòng (`rag/ingestion.py:120`).
- Khái niệm mới thì định nghĩa ngắn ngay lần đầu xuất hiện, kèm link tài liệu chính thức đã đọc.
- Chi tiết không có nghĩa là dài. Mỗi câu phải mang thông tin; dùng bảng cho so sánh, đoạn văn cho lập luận.

## Checklist trước khi coi là xong

- [ ] Mọi file thêm/sửa/xóa đều có đường dẫn trong "Thay đổi cụ thể" / "Cách sửa"
- [ ] Nguyên nhân phân biệt trực tiếp và gốc (với bug: có bằng chứng)
- [ ] Ít nhất 2 phương án thay thế thật, có bảng "khi nào dùng"
- [ ] Trade-off nêu cả cái mất
- [ ] Rủi ro còn lại được nêu thẳng
- [ ] Bằng chứng là output nguyên văn, hoặc ghi rõ CHƯA VERIFY + lệnh
- [ ] Có check chống tái phạm, hoặc lý do chưa có + đưa vào việc còn mở
- [ ] Không có secret
- [ ] Frontmatter đã cập nhật trạng thái và mức chắc chắn

## Liên hệ với tài liệu khác

- `docs/GHI_CHU_NGAYxx.md` vẫn là tổng kết của một ngày roadmap. Nó trỏ tới các nhật ký task của ngày đó, không chép lại nội dung.
- Bug thuộc lớp mới, hoặc quyết định kỹ thuật mới: thêm vào "Việc còn mở" việc cập nhật `docs/context/05_failure_patterns.md` hoặc `docs/context/07_decisions_log.md`.

## Hook bắt buộc ghi nhật ký

`.claude/hooks/nhat_ky_guard.py` chạy ở SessionStart và Stop:

- Chụp nội dung mọi file tracked và untracked (bỏ qua file ignored và `docs/nhat-ky/`). Commit không tính là thay đổi; sửa bằng bất kỳ cách nào (Edit, sed, script, IDE) đều bị phát hiện.
- Cuối mỗi lượt, mỗi file thay đổi kể từ lần xác nhận trước phải được **nêu đường dẫn** trong một file nhật ký vừa tạo hoặc sửa. Nêu thư mục cha sâu từ 2 cấp, có dấu `/` cuối (vd `docs/context/`), cũng được tính. Thiếu thì hook yêu cầu ghi nhật ký trước khi kết thúc lượt.
- Thay đổi chưa ghi không được tha khi sang phiên mới: SessionStart nhắc lại.
- Xem tình trạng: `python3 .claude/hooks/nhat_ky_guard.py status`.
- Lối thoát chỉ dành cho Pan (vd sau `git pull`, hay `git checkout` sang nhánh khác): Pan tự chạy `python3 .claude/hooks/nhat_ky_guard.py ack "lý do"` trong terminal. Lệnh này bị chặn với Claude và được ghi log ở `.git/nhat-ky-guard.log`.

Hook chỉ kiểm được nhật ký *có nêu file*, không kiểm được *phân tích có tốt không*. Đừng viết nhật ký chỉ để qua hook: phần phân tích mới là thứ Pan cần.
