# Tevind deploy is a repo to make Frappe Docker Servers simple to manage
It is a general purpose repo meant to be deployed, and help deploy frappe apps on an Ubuntu 24 OVH-cloud VPS server through ssh. 

## Structure
Based on the previous frappe_deploy repo. But now it has callable python-scripts as endpoints, and clear config-files for the server and local .env for git keys.

## Workflow
### Setup
There is a conenction to the target server through SSH. The user (or app) that is connected on the server creates a folder "tevind_deploy" in the home folder. From there it pulls/paste this folder. From there it runs the tevind_deploy_setup pythonscript. The script checks dependensies and installs any missing ones. 

### Usage
SSH into the folder again. From there there should now be some functions to call. 
- Basic frappe bench-wrappers (check installed apps, sites etc)
- Get tevind_deploy config info functions (similar info as in todays apps.json and boostrap-sites.sh)
- Set config info fucntions and repo ssh keys
- automatic check and configure workflows based on the config
- missing ssh keys are retreived through functions over SSH (user provides them if missing)

## Notes
This is built to be completely used through SSH comands, so that it can be used by an agent on another app. Basic MCP tools should therefore also be provided