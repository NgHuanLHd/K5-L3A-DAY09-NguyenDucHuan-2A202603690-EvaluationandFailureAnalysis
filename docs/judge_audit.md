# Judge audit

> **Đọc nhanh.** Trước khi để một LLM chấm bài (judge), ta chấm chính judge đó bằng cách so với người chấm trên 100 câu trả lời. Judge **giọng điệu** đạt chuẩn: bắt được 80% câu có giọng không phù hợp, ít báo oan. Judge **hữu ích** bị loại: nó bỏ sót 21/30 câu trả lời kém — phần lớn là câu nói sai số tiền, thứ judge không thể thấy vì không đọc sổ cái. Việc kiểm số tiền vì vậy giao cho code. Thuật ngữ (precision, recall, κ…): [thuat_ngu.md](thuat_ngu.md).

Chạy: `python -m lab9.judge --csv <file> --audit 5`. FAIL là lớp dương.

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | PASS (tp 24, fp 4, fn 6, tn 66) | **dùng được** — chưa đưa vào gate (non-blocking khi thêm) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **FAIL** (tp 9, fp 0, fn 21, tn 70) | **loại** |

Rogan-Gladen: tone 0.28 → 0.30; helpful 0.09 → 0.30 (judge báo thấp hơn thật 3 lần).

## ≥5 case audit thủ công

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | Người | Thêm quy tắc xưng hô "LogiX – anh/chị"; cấm em/bạn/nha. |
| 2 | H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | Người | Chấm cả câu, không theo từ lịch sự mở đầu. |
| 3 | H-042 "…đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Ghi rõ: xin lỗi không bắt buộc. |
| 4 | H-051 "…đã refund 90.000đ…" | FAIL | PASS | Người | Whitelist thuật ngữ được phép (refund, ship). |
| 5 | H-081 "…chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** | Nhãn người sai → sửa nhãn, không sửa rubric. |
| 6 | H-094 "Da, LogiX da hoan…" (mất dấu) | PASS | FAIL | Người | Thêm tiêu chí tiếng Việt có dấu. |
| 7 | HL-015 "đã hoàn 55.000đ" (ledger 40.000đ) | PASS | FAIL | Người | Judge không thấy ledger; 18/21 fn của helpful là loại này → chuyển sang code (`reply_amount_matches_ledger`). |
| 8 | HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." | PASS | FAIL | Người | PASS chỉ khi nêu rõ kết quả (đã hoàn X / từ chối vì Y / cần Z). |

## Kết luận

- **helpful bị loại**: R 0.30 — bỏ sót 21/30 reply không hữu ích; κ 0.375. P 1.0 không giúp được vì gate cần *bắt* lỗi. 18/21 fn là sai số tiền — lỗi judge không thể thấy, đã giao cho grader code.
- **tone_ok** đạt sàn; 6 fn còn lại sửa được bằng rubric (case 1, 2, 6).
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai; cần nhãn người mới để có held-out thật (case 5 cho thấy nhãn người cũng sai).
