# python-ml-backend

"""
Invoke-RestMethod -Method Post `
>>   -Uri "http://127.0.0.1:8080/api/report/generate" `
>>   -ContentType "application/json" `
>>   -Body '{"project_id":148,"user_id":158}'
>> 

"""

## Desktop EXE security

- Keep `DB_ACCESS_MODE=backend`.
- Do not ship `DATABASE_URL`, `DB_USER`, `DB_PASSWORD` in `.env`.
- Desktop Python must call C# APIs (`SIGNAL_TRACKERS_API_URL`) instead of direct DB access.
- Use `python/.env.example` as the safe template for packaged builds.
