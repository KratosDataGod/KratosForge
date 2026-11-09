# Group Policy templates

`KratosForge.admx` and `en-US/KratosForge.adml` describe the KratosForge policy settings for Group Policy and Intune. They're generated from `sidecar/fabric_desktop/policy_schema.py`, which describes every key in `app_settings.POLICY_KEYS`, so don't edit them by hand. To regenerate them after a setting changes:

```
python -m fabric_desktop.policy_schema
```

`sidecar/tests/test_policy_schema.py` fails in these cases:
- a setting the app reads has no description;
- a description names a setting the app doesn't read;
- a setting is missing from the ADMX;
- these files differ from what the generator writes.

## What the templates set

Every policy is a **Computer** policy. It writes `HKLM\Software\Policies\KratosBI\KratosForge`, and the value name is the policy file's key:
- **On/off settings:** a DWORD, 1 or 0.
- **Numbers:** a DWORD within the setting's limits.
- **Choices and text:** a string.
- **Lists:** a subkey named after the setting, with one string value per item.

Two settings hold structured values, `platform` and `incident_sla`. A template can't express them, so they stay in `policy.json` ([Enterprise install](../../docs/enterprise-install.md)).

## Import

- **Group Policy (domain):** copy `KratosForge.admx` to `\\<domain>\SYSVOL\<domain>\Policies\PolicyDefinitions\` and `en-US\KratosForge.adml` to its `en-US` folder (the central store). The settings then appear under **Computer Configuration > Policies > Administrative Templates > KratosForge**.
- **One PC:** copy them to `C:\Windows\PolicyDefinitions\` and `C:\Windows\PolicyDefinitions\en-US\`, then open `gpedit.msc`.
- **Intune:** go to **Devices > Configuration > Import ADMX**. Upload `KratosForge.admx` with `KratosForge.adml`, then create a profile from **Templates > Imported Administrative templates**.
