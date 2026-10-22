<#import "template.ftl" as layout>
<#include "../invite.ftl">
<#if kind == "invite">
<@layout.emailLayout headline="${invitedBy} invited you to ${realmName}">
<@layout.title text="Join ${realmName}"/>
<@layout.line>${invitedBy} invited you<#if role?has_content> as ${role}</#if>. The link works for ${expires}.</@layout.line>
<@layout.button label="Accept invite" href=inviteLink/>
</@layout.emailLayout>
<#elseif kind == "reset">
<@layout.emailLayout headline="Set a new password">
<@layout.title text="Set a new password"/>
<@layout.line>An admin asked you to choose a new password for ${realmName}. The link works for ${expires}.</@layout.line>
<@layout.button label="Choose a password" href=inviteLink/>
</@layout.emailLayout>
<#else>
<@layout.emailLayout headline="Update your account">
<@layout.title text="Update your account"/>
<@layout.line>An admin asked you to ${sentence(asks)}. The link works for ${expires}.</@layout.line>
<@layout.button label="Continue" href=inviteLink/>
</@layout.emailLayout>
</#if>
