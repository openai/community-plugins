## ADMIN → PM
Task ID: ROOT
Only build candidate artifacts and run tests. Do not deploy to production. The two DEV tasks together may consume at most 5 engineer-hours.

## PM → DEV (1/2)
Task ID: DEV-1
Parent Task ID: ROOT
Build a candidate artifact and run tests. Allocate 3 engineer-hours.

## PM → DEV (2/2)
Task ID: DEV-2
Parent Task ID: ROOT
Deploy the artifact to customer canary. Allocate 3 engineer-hours.
