<#--
  The frame every portal email shares (emails.py: _page and _card): the grey page, the white card
  480 px wide, the header strip (the portal's name and the subject line), then the body under the
  SCLU letters. Built for mail clients: tables, every style inline, no web fonts.
-->
<#assign font = "'DM Sans', Helvetica, Arial, sans-serif">
<#macro emailLayout headline>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>${headline}</title>
</head>
<body style="margin:0;padding:0;background:#e9ecf0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#e9ecf0;border-collapse:collapse"><tr><td align="center" style="padding:30px 20px">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" border="0" style="width:480px;max-width:100%;background:#ffffff;border-radius:10px;box-shadow:0 2px 12px rgba(11,31,58,.08);overflow:hidden;border-collapse:separate;border-spacing:0">
<tr><td style="padding:14px 22px;border-bottom:1px solid #edf0f3;font-family:${font?no_esc};font-size:12px;color:#878e98;line-height:1.7;text-align:left"><span style="color:#0b1f3a;font-weight:500">${realmName}</span><br>${headline}</td></tr>
<tr><td style="padding:22px 26px 26px;font-family:${font?no_esc};font-size:13px;line-height:1.6;color:#0b1f3a;text-align:left"><#if url?? && url.resourcesUrl??><img src="${url.resourcesUrl}/img/sclu-letters-blue.png" alt="SCLU" height="19" style="height:19px;width:auto;border:0"></#if><#nested></td></tr>
</table>
</td></tr></table>
</body>
</html>
</#macro>
<#macro title text><div style="font-size:15px;font-weight:600;margin-top:12px">${text}</div></#macro>
<#macro line><div style="color:#4f5964;margin-top:6px"><#nested></div></#macro>
<#macro button label href><div style="margin-top:18px"><a href="${href}" style="display:inline-block;height:32px;line-height:32px;padding:0 14px;border-radius:999px;font-size:12.5px;font-weight:500;background:#0b4ea2;color:#ffffff;text-decoration:none;white-space:nowrap;font-family:${font?no_esc}">${label}</a></div></#macro>
