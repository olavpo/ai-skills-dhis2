# Bug in system settings API??

## Background

I was trying to find out where the CORS whitelist is stored on our test server
(https://your-instance.org, DHIS2 2.42.5.2, I think the build was sometime in
spring) because our dashboard tool could not log in from the browser. First I
looked in the System Settings app, then in the docs, then I tried a few
endpoints with curl. I tried /api/systemSettings first, which worked and gave
me a big JSON, and then I tried to get the single key.

## What happens

### The GET call

When I do this:

    curl -u admin:district "https://your-instance.org/api/systemSettings/keyCorsWhitelist"

I get back a huge HTML page, status 500, with a Java stack trace in it. The
first lines are:

    jakarta.servlet.ServletException: Request processing failed:
    org.hisp.dhis.feedback.NotFoundException: Setting does not exist: keyCorsWhitelist
        at org.hisp.dhis.webapi.controller.SystemSettingsController.checkKeyExists(SystemSettingsController.java:262)

This looked to me like my request was malformed, so I spent about an hour
changing headers (Accept: text/plain, Accept: application/json, adding .json)
but it was always the same 500.

### The POST call

Then, trying to set it, I did:

    curl -u admin:district -X POST -H "Content-Type: text/plain" \
      -d "http://localhost:3000" \
      "https://your-instance.org/api/systemSettings/keyCorsWhitelist"

and this one returned a clean JSON 404:

    {"httpStatus":"Not Found","httpStatusCode":404,"status":"ERROR","errorCode":"E1005"}

### Other things

I think this probably happens for all the systemSettings endpoints and maybe
for userSettings too, and it is possibly a big security problem because the
stack trace exposes internal class names. It might be related to the Spring 6
upgrade, or the new settings refactoring, or maybe the jakarta migration, I am
not sure. It could also be a Tomcat configuration thing on our server.

## What should happen

It should not give a 500 and it should work properly and be consistent.

## Priority

I would say Critical / Blocker because it blocks our integration.
