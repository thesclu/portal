<#import "template.ftl" as layout>
<#-- Forgot password: the portal's own words (backend/portal/core/services/invites.py), in its frame. -->
<@layout.emailLayout headline="Reset your portal password">
<@layout.title text="Reset your password"/>
<@layout.line>Someone asked to reset the password for this address. The link works for ${linkExpirationFormatter(linkExpiration)}. If you did not ask, ignore this.</@layout.line>
<@layout.button label="Choose a new password" href=link/>
</@layout.emailLayout>
