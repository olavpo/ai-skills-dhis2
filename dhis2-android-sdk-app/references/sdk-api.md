# DHIS2 Android SDK: calls and behaviours

Verified against SDK 1.14.2 on DHIS2 2.42.6 (compiled and run on an emulator). Blocking variants
shown; call them off the main thread (`withContext(Dispatchers.IO)`).

## Contents
1. Setup and session
2. Metadata
3. Creating and editing tracker data
4. Reading
5. Upload, conflicts, sync state
6. Download, search, wipe
7. Behaviours worth knowing

## 1. Setup and session

```kotlin
val d2 = D2Manager.blockingInstantiateD2(
    D2Configuration.builder()
        .appName("my-app").appVersion(BuildConfig.VERSION_NAME)
        .context(context.applicationContext)
        .readTimeoutInSeconds(60).connectTimeoutInSeconds(30).writeTimeoutInSeconds(60)
        .build())!!

d2.userModule().blockingIsLogged()
d2.userModule().blockingLogIn(username, password, serverUrl)   // falls back to offline login by itself
d2.userModule().blockingLogOut()
d2.userModule().user().blockingGet()?.username()
```

Login errors are `D2Error` (walk the cause chain) with `D2ErrorCode`: `BAD_CREDENTIALS`,
`USER_ACCOUNT_DISABLED`, `USER_ACCOUNT_LOCKED`, `NO_DHIS2_SERVER`, `SERVER_URL_MALFORMED`,
`UNKNOWN_HOST`, `SOCKET_TIMEOUT`, `NO_AUTHENTICATED_USER_OFFLINE`. (`DIFFERENT_AUTHENTICATED_USER_OFFLINE`
is deprecated.) The SDK keeps one database per account.

## 2. Metadata

```kotlin
d2.metadataModule().blockingDownload()     // also applies the server's encryptDB setting
d2.programModule().programs().uid(p).blockingGet()
d2.programModule().programTrackedEntityAttributes().byProgram().eq(p)
    .orderBySortOrder(RepositoryScope.OrderByDirection.ASC).blockingGet()     // .mandatory()
d2.programModule().programStageDataElements().byProgramStage().eq(stage)
    .orderBySortOrder(RepositoryScope.OrderByDirection.ASC).blockingGet()     // .compulsory()
d2.dataElementModule().dataElements().uid(de).blockingGet()                    // valueType(), optionSet(), displayFormName()
d2.trackedEntityModule().trackedEntityAttributes().uid(a).blockingGet()        // pattern(), generated()
d2.optionModule().options().byOptionSetUid().eq(os).orderBySortOrder(ASC).blockingGet()
d2.programModule().programStages().uid(s).blockingGet()                        // minDaysFromStart(), autoGenerateEvent(), …
d2.programModule().programRules().byProgramUid().eq(p).withProgramRuleActions().blockingGet()
d2.programModule().programRuleVariables().byProgramUid().eq(p).blockingGet()
d2.constantModule().constants().blockingGet()
d2.relationshipModule().relationshipTypes().withConstraints().uid(rt).blockingGet()
d2.organisationUnitModule().organisationUnits()
    .byProgramUids(listOf(p)).byOrganisationUnitScope(OrganisationUnit.Scope.SCOPE_DATA_CAPTURE).blockingGet()
```

Reserved (generated) values:

```kotlin
val rv = d2.trackedEntityModule().reservedValueManager()
rv.blockingDownloadReservedValues(attributeUid, 60)   // fills up to 60 for EVERY coded org unit of the
                                                      // attribute's programs in the capture scope
rv.blockingCount(attributeUid, orgUnitUid)
rv.blockingGetValue(attributeUid, orgUnitUid)          // tops up if online; throws NO_RESERVED_VALUES when empty offline
```

There is no public per-org-unit download; org units without a code are skipped when the pattern
uses `ORG_UNIT_CODE`. The user's capture scope is the only lever on how much is reserved.

## 3. Creating and editing tracker data

```kotlin
val teis = d2.trackedEntityModule().trackedEntityInstances()
val tei = teis.blockingAdd(TrackedEntityInstanceCreateProjection.create(orgUnit, trackedEntityType))

val enrollments = d2.enrollmentModule().enrollments()
val enr = enrollments.blockingAdd(EnrollmentCreateProjection.create(orgUnit, program, tei))
enrollments.uid(enr).setEnrollmentDate(date); enrollments.uid(enr).setIncidentDate(date)
enrollments.uid(enr).setGeometry(GeometryHelper.createPointGeometry(longitude, latitude))  // lon first
enrollments.uid(enr).setStatus(EnrollmentStatus.COMPLETED)

val events = d2.eventModule().events()
val ev = events.blockingAdd(EventCreateProjection.create(enr, program, stage, orgUnit, null))
events.uid(ev).setEventDate(date)                      // new events are ACTIVE
events.uid(ev).setStatus(EventStatus.COMPLETED); events.uid(ev).setCompletedDate(Date())
// scheduled event: no event date, a due date, SCHEDULE
events.uid(ev).setEventDate(null); events.uid(ev).setDueDate(due); events.uid(ev).setStatus(EventStatus.SCHEDULE)

d2.trackedEntityModule().trackedEntityAttributeValues().value(attr, tei).blockingSet(v)   // or blockingDeleteIfExist()
d2.trackedEntityModule().trackedEntityDataValues().value(ev, de).blockingSet(v)           // or blockingDeleteIfExist()

val rel = d2.relationshipModule().relationships()
    .blockingAdd(RelationshipHelper.teiToTeiRelationship(fromTei, toTei, relationshipType))

// moving to another org unit: every object
teis.uid(tei).setOrganisationUnitUid(ou); enrollments.uid(enr).setOrganisationUnitUid(ou); events.uid(ev).setOrganisationUnitUid(ou)

// deleting
events.uid(ev).blockingDelete(); enrollments.uid(enr).blockingDelete(); teis.uid(tei).blockingDelete()
d2.relationshipModule().relationships().uid(relUid).blockingDelete()
```

`blockingSet` does **not** validate the value against its value type (SDK 1.14.2 stores "abc" in an
integer field; the server rejects it on upload). Validate in the app first, e.g.
`valueType.validator.validate(value)` (returns a `Result`; the failure is a per-type sealed class such as `IntegerFailure`).
Mandatory attributes and data elements are likewise only checked by the server on upload.

## 4. Reading

```kotlin
teis.withTrackedEntityAttributeValues().uid(tei).blockingGet()
d2.trackedEntityModule().trackedEntityAttributeValues().byTrackedEntityInstance().eq(tei).blockingGet()
enrollments.byTrackedEntityInstance().eq(tei).byProgram().eq(p).byDeleted().isFalse.blockingGet()
events.byEnrollmentUid().eq(enr).byProgramStageUid().eq(stage).byDeleted().isFalse
    .withTrackedEntityDataValues().blockingGet()
d2.trackedEntityModule().trackedEntityDataValues().byEvent().eq(ev).blockingGet()
d2.relationshipModule().relationships().getByItem(RelationshipHelper.teiItem(tei))        // list
d2.relationshipModule().relationships().getByItem(RelationshipHelper.teiItem(tei), true, false) // include deleted
enrollment.geometry()?.coordinates()   // "[lon, lat]" string for a point
```

Sort events yourself if order matters (`createdAtClient() ?: created()`, then uid).

## 5. Upload, conflicts, sync state

```kotlin
teis.byUid().`in`(listOf(unitTei) + memberTeis).blockingUpload()   // only these, and their
                                                                    // enrollments, events, relationships
d2.importModule().trackerImportConflicts().byTrackedEntityInstanceUid().`in`(teis).blockingGet()
// conflict: errorCode(), displayDescription(), conflict(), value(), event(), enrollment(),
//           trackedEntityInstance(), dataElement(), trackedEntityAttribute(), created()
teis.uid(t).blockingGet()?.syncState()             // TO_POST, TO_UPDATE, SYNCED, ERROR, WARNING, UPLOADING, …
teis.uid(t).blockingGet()?.aggregatedSyncState()   // worst state over the TE and its children
```

- Decide "sent" on `aggregatedSyncState == SYNCED` for every tracked entity of the unit of work;
  "needs attention" on any `ERROR`/`WARNING`; anything else = not sent yet (network).
- A never-uploaded TE reads `syncState=TO_POST` but `aggregatedSyncState=TO_UPDATE`. To check that
  something was *not* uploaded, test `syncState()`.
- A synced TE deleted locally stays as `deleted=true, TO_UPDATE` until its deletion is uploaded,
  then **disappears locally** — bookkeeping that re-reads the uploaded list must treat missing rows
  as done. Deletions of members not linked from anything are easy to forget in the upload list;
  keep a way to find them (e.g. a back-link attribute).
- Conflict codes seen: E1018/E1019 mandatory missing; E1020/E1021 future dates; E1007/E1302
  invalid value; E1063/E1064 duplicate unique value; E1000/E1003 no access to org unit; E1300 rule
  on server; E1100/E1103/E1083 no authority to delete (`F_TEI_CASCADE_DELETE`,
  `F_ENROLLMENT_CASCADE_DELETE`). The first conflict is often a follow-on ("event … cannot be
  created because enrollment … could not be created"): prefer root-cause codes when showing one.

## 6. Download, search, wipe

```kotlin
d2.trackedEntityModule().trackedEntityInstanceDownloader()
    .byUid().`in`(uids).byProgramUid(p).blockingDownload()      // single records; won't overwrite unsent local changes

d2.trackedEntityModule().trackedEntitySearch()
    .byProgram().eq(p)
    .byFilter(attr).eq(v)                  // byAttribute(attr).eq(v) also exists
    .offlineOnly()                         // or offlineFirst(), onlineFirst(), onlineOnly()
    .blockingGet()                         // TrackedEntitySearchItem: uid, attributeValues, isOnline, …

d2.wipeModule().wipeData()                 // tracker data AND reserved values; metadata stays
d2.wipeModule().wipeEverything()           // everything
```

Online search only accepts attributes marked searchable in the program; non-searchable filters
work offline only. The app never downloads tracker data unless it calls the downloader: for a
create-only app, don't call it and nothing is downloaded.

## 7. Behaviours worth knowing

- `atomicMode=OBJECT` on every upload (`TrackerImporterService`): a batch can import partially.
- A relationship's other end is added to the same payload if not yet synced (payload generator).
- The SDK does not generate auto-generated events.
- `upload()` respects repository filters.
- Database encryption follows the server's Android Settings app (`encryptDB` in general
  settings), applied during metadata download; the app cannot switch it itself.
- Enrollment point geometry uploads fine on 2.42.6 through the SDK (unlike some raw API payloads,
  which got E1074).
- AGP needs `isCoreLibraryDesugaringEnabled = true`.
