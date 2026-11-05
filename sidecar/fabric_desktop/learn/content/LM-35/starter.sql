SELECT DISTINCT job, 'failed' AS reason FROM job_runs WHERE status = 'failed' ORDER BY job;
