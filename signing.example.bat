@echo off
rem ============================================================
rem  signing.example.bat -- template for code-signing config
rem ============================================================
rem  Copy this file to signing.bat (git-ignored) and fill in ONE
rem  of the variants below. make_installer.bat calls signing.bat
rem  if it exists; when EQL_SIGN is set it signs the four tool
rem  EXEs and tells Inno Setup to sign the installer/uninstaller.
rem
rem  EQL_SIGN must be a complete "signtool sign ..." command
rem  prefix -- the file to sign gets appended after it.
rem
rem  signtool.exe ships with the Windows SDK ("Windows SDK Signing
rem  Tools for Desktop Apps" component) -- adjust the path below to
rem  the version installed on this machine, or put it on PATH.
rem
rem  ALWAYS include a timestamp (/tr + /td): signatures must stay
rem  valid after the certificate expires.
rem ============================================================

set "SIGNTOOL=C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe"

rem -- Variant 1: certificate in the Windows cert store (USB token /
rem    Certum SimplySign / imported cert), selected by SHA1 thumbprint.
rem    Find the thumbprint in certmgr.msc -> Personal -> Certificates.
rem set EQL_SIGN="%SIGNTOOL%" sign /sha1 YOUR_CERT_THUMBPRINT /fd SHA256 /tr http://time.certum.pl /td SHA256

rem -- Variant 2: select by subject name instead of thumbprint.
rem set EQL_SIGN="%SIGNTOOL%" sign /n "Your Name Or Company" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256

rem -- Variant 3: Azure Trusted Signing ($9.99/mo, Microsoft-vetted
rem    identity -- best SmartScreen standing). Needs the Trusted Signing
rem    dlib package and a metadata.json naming your account/profile:
rem    https://learn.microsoft.com/azure/trusted-signing/
rem set EQL_SIGN="%SIGNTOOL%" sign /v /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 /dlib "C:\path\to\Azure.CodeSigning.Dlib.dll" /dmdf "C:\path\to\metadata.json"
