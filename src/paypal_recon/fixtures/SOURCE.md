# Fixture shape source

`transactions.json` follows the documented response of `GET /v1/reporting/transactions` (Transaction Search v1). The shape was read anonymously from PayPal's public OpenAPI specifications:

- Repository: `paypal/paypal-rest-api-specifications`
- File: `openapi/reporting_transactions_v1.json`
- Commit: `90e8041ffe02d80c452d2b476bedd59a8d219bdc` (main at the time of reading)

Fields used: `transaction_details[]`, `page`, `total_items`, `total_pages`, `links`; per entry `transaction_info` (`transaction_id`, `transaction_event_code`, `transaction_initiation_date`, `transaction_amount{currency_code,value}`, `transaction_status`, `invoice_id`, `paypal_reference_id`, `transaction_subject`, `transaction_note`, `custom_field`) and `payer_info.account_id`.

The values are invented. No `developer.paypal.com` page was fetched, so no robots.txt check applied.
