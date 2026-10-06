---
ngay: {{ngay}}
gio: "{{gio}}"
loai: thay-doi
tieu_de: "{{tieu_de}}"
trang_thai: dang-lam          # dang-lam | xong | treo
muc_do_chac_chan: chua-verify # da-xac-minh | co-dau-hieu | chua-verify
nhanh: {{nhanh}}
commit_goc: {{commit}}
lien_quan: []
---

# {{tieu_de}}

## 1. Tóm tắt
<!-- 2-4 câu: đã làm gì, vì sao, kết quả hiện tại (đã verify hay chưa). Đọc mỗi mục này là đủ biết task. -->

## 2. Bối cảnh và mục tiêu
<!-- Vấn đề gốc là gì, ai/điều gì yêu cầu, ràng buộc (môi trường, version, bảo mật, thời gian), tiêu chí "xong". -->

## 3. Thay đổi cụ thể
<!-- Nêu ĐÚNG đường dẫn mọi file thêm/sửa/xóa (hook nhat_ky_guard kiểm tra). Kèm lệnh đã chạy nếu có. -->

| File | Thay đổi | Lý do |
|---|---|---|

## 4. Vì sao chọn cách này
<!-- Cơ chế hoạt động bên dưới, khái niệm nền cần hiểu, vì sao cơ chế đó khớp với vấn đề ở mục 2. -->

## 5. Phương án thay thế
<!-- Ít nhất 2 phương án THẬT (kể cả "không làm gì" nếu hợp lý). Mỗi cái: mô tả, ưu, nhược, vì sao không chọn LÚC NÀY. -->

## 6. Khi nào dùng cái nào
<!-- Điều kiện cụ thể (quy mô, môi trường, mức rủi ro, giai đoạn roadmap) -> lựa chọn hợp lý. Không viết "tùy trường hợp". -->

| Tình huống / điều kiện | Lựa chọn hợp lý | Lý do |
|---|---|---|

## 7. Trade-off
<!-- Được gì / mất gì: độ phức tạp, hiệu năng, chi phí, bảo trì, bảo mật, trải nghiệm, khả năng học. Phải có cái MẤT. -->

## 8. Hệ quả và tác động
<!-- Ảnh hưởng tới phần nào khác của hệ thống, cái gì phụ thuộc vào thay đổi này, cách hoàn tác nếu sai. -->

## 9. Rủi ro và giới hạn còn lại
<!-- Thay đổi này KHÔNG giải quyết được gì? Trường hợp nào nó vẫn hỏng? -->

## 10. Bằng chứng verify
<!-- Lệnh + output NGUYÊN VĂN, trước/sau. Chưa verify thì ghi CHƯA VERIFY + lệnh cần chạy + chạy ở đâu (local/cloud). -->

## 11. Giữ cho đúng về sau
<!-- Test / preflight / rule / hook nào bắt được nếu hỏng lại. Chưa có thì nêu lý do và đưa vào mục 13. -->

## 12. Bài học và kiến thức rút ra
<!-- Giải thích để Pan HIỂU, dùng chính code/bug của repo. Liên hệ docs/context/05 (lớp bug), 07 (quyết định). -->

## 13. Việc còn mở
- [ ]

## 14. Liên kết
<!-- Commit, GHI_CHU_NGAYxx, nhật ký liên quan, tài liệu chính thức đã đọc (kèm link). -->

## Tiến trình
- {{ngay}} {{gio}}: tạo nhật ký.
