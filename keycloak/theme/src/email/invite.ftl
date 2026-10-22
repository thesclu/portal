<#--
  Shared by html/ and text/executeActions.ftl: who this message is to and what it asks.
  An invite when the portal recorded who invited the person (the user attributes it keeps:
  sclu_invited_by, sclu_role_label); a password an admin asked to be reset when the only action is
  UPDATE_PASSWORD and nobody invited them; otherwise the admin's request, in plain words.
-->
<#assign invitedBy = ((user.attributes.sclu_invited_by)!'')?trim>
<#assign role = ((user.attributes.sclu_role_label)!'')?trim>
<#assign actions = requiredActions![]>
<#if invitedBy?has_content>
  <#assign kind = "invite">
<#elseif actions?size == 1 && actions[0] == "UPDATE_PASSWORD">
  <#assign kind = "reset">
<#else>
  <#assign kind = "update">
</#if>
<#assign asks = []>
<#list actions as action>
  <#switch action>
    <#case "UPDATE_PASSWORD"><#assign asks = asks + ["choose a new password"]><#break>
    <#case "webauthn-register"><#assign asks = asks + ["add a passkey"]><#break>
    <#case "CONFIGURE_RECOVERY_AUTHN_CODES"><#assign asks = asks + ["save recovery codes"]><#break>
    <#case "CONFIGURE_TOTP"><#assign asks = asks + ["set up an authenticator app"]><#break>
    <#case "VERIFY_EMAIL"><#assign asks = asks + ["confirm your email"]><#break>
    <#default><#assign asks = asks + ["update your account"]>
  </#switch>
</#list>
<#function sentence parts>
  <#if parts?size == 0><#return "update your account"></#if>
  <#if parts?size == 1><#return parts[0]></#if>
  <#return parts[0..(parts?size - 2)]?join(", ") + " and " + parts?last>
</#function>
<#assign expires = linkExpirationFormatter(linkExpiration)>
<#-- The sign-in page reads who invited the person from the link's fragment, which never reaches
     a server (keycloak/theme/src/login/lib.ts, rememberInvite). -->
<#if kind == "invite">
  <#assign inviteLink = link + "#invited_by=" + invitedBy?url("UTF-8") + "&role=" + role?url("UTF-8")>
<#else>
  <#assign inviteLink = link>
</#if>
