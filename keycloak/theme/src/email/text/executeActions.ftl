<#ftl output_format="plainText">
<#include "../invite.ftl">
<#if kind == "invite">
${invitedBy} invited you to ${realmName}<#if role?has_content> as ${role}</#if>.

Join here: ${inviteLink}

The link works once, for ${expires}.
<#elseif kind == "reset">
An admin asked you to choose a new password for ${realmName}.

Choose one here: ${inviteLink}

The link works for ${expires}.
<#else>
An admin asked you to ${sentence(asks)} for ${realmName}.

Continue here: ${inviteLink}

The link works for ${expires}.
</#if>
