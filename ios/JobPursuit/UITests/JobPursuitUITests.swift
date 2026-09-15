import XCTest

final class JobPursuitUITests: XCTestCase {
    override func setUpWithError() throws { continueAfterFailure = false }
    func testPreviewNavigationAndHonestStates() {
        let app = XCUIApplication(); app.launchArguments = ["--preview", "--uitesting"]; app.launch()
        XCTAssertTrue(app.staticTexts["Your next move,\nAlex."].waitForExistence(timeout: 10))
        saveScreenshot("01-Today")
        app.tabBars.buttons["Discover"].tap()
        XCTAssertTrue(app.textFields["discover.search"].waitForExistence(timeout: 5))
        assertPreviewClearsNavigation(app, title: "Discover")
        saveScreenshot("02-Discover")
        app.staticTexts["Senior Backend Engineer"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Prepare my application"].waitForExistence(timeout: 5))
        assertPreviewClearsNavigation(app, title: "Meridian")
        saveScreenshot("03-Opportunity")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.tabBars.buttons["Studio"].tap()
        XCTAssertTrue(app.staticTexts["Your story.\nBeautifully told."].waitForExistence(timeout: 5))
        assertPreviewClearsNavigation(app, title: "Studio")
        saveScreenshot("04-Studio")
        app.tabBars.buttons["Tracker"].tap()
        XCTAssertTrue(app.staticTexts["Needs you"].waitForExistence(timeout: 5))
        assertPreviewClearsNavigation(app, title: "Tracker")
        saveScreenshot("05-Tracker")
        app.staticTexts["Needs you"].tap()
        XCTAssertTrue(app.staticTexts["Only you know\nthis part."].waitForExistence(timeout: 5))
        saveScreenshot("06-Questions")
    }
    func testWelcomeScreen() {
        let app = XCUIApplication(); app.launchArguments = ["--uitesting"]; app.launch()
        XCTAssertTrue(app.textFields["auth.email"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["Continue with email"].isEnabled)
        saveScreenshot("00-Welcome")
    }
    func testProfessionNeutralProfileAndCredentialConfirmation() {
        let app = XCUIApplication(); app.launchArguments = ["--preview", "--uitesting"]; app.launch()
        XCTAssertTrue(app.buttons["Your profile"].waitForExistence(timeout: 10))
        app.buttons["Your profile"].tap()
        let profession = app.textFields["profile.profession"]
        XCTAssertTrue(profession.waitForExistence(timeout: 5))
        profession.tap(); profession.typeText("Nursing")
        let add = app.buttons["profile.addQualification"]
        scrollTo(add, in: app)
        XCTAssertTrue(add.waitForExistence(timeout: 5)); add.tap()
        let name = app.textFields["qualification.name"]
        XCTAssertTrue(name.waitForExistence(timeout: 5))
        name.tap(); name.typeText("Example nursing licence")
        scrollTo(app.buttons["qualification.confirm"], in: app)
        XCTAssertTrue(app.buttons["qualification.confirm"].exists)
        XCTAssertFalse(app.buttons["qualification.confirm"].isEnabled)
        saveScreenshot("07-Qualification-confirmation")
        app.navigationBars["Qualification"].buttons["Cancel"].tap()
        XCTAssertTrue(app.navigationBars["Your profile"].waitForExistence(timeout: 5))
        // Returning from an unsaved child editor must not reset the parent draft.
        scrollTo(profession, in: app)
        XCTAssertEqual(profession.value as? String, "Nursing")
        XCTAssertFalse(app.staticTexts["Example nursing licence"].exists)
        saveScreenshot("08-Profile-draft-preserved")
    }
    func testEligibilityReviewRequiresReasonAndExplicitConfirmation() {
        let app = XCUIApplication(); app.launchArguments = ["--preview", "--uitesting"]; app.launch()
        XCTAssertTrue(app.tabBars.buttons["Discover"].waitForExistence(timeout: 10))
        app.tabBars.buttons["Discover"].tap()
        app.staticTexts["Senior Backend Engineer"].firstMatch.tap()
        let review = app.buttons["job.reviewEligibility"]
        scrollTo(review, in: app)
        XCTAssertTrue(review.waitForExistence(timeout: 5)); review.tap()
        XCTAssertTrue(app.navigationBars["Eligibility review"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["eligibility.disclaimer"].label.contains("Not independently verified"))
        let save = app.buttons["eligibility.save"]
        scrollTo(save, in: app) // Form rows are lazily exposed only after scrolling.
        XCTAssertTrue(save.waitForExistence(timeout: 5))
        XCTAssertFalse(save.isEnabled)
        // A multiline SwiftUI TextField can expose a UITextView on iOS.
        let reason = app.descendants(matching: .any).matching(identifier: "eligibility.reason").firstMatch
        scrollTo(reason, in: app)
        XCTAssertTrue(reason.exists); reason.tap(); reason.typeText("Sponsorship still needs confirmation.")
        scrollTo(app.switches["eligibility.confirm"], in: app)
        XCTAssertFalse(save.isEnabled)
        turnOn(app.switches["eligibility.confirm"])
        scrollTo(save, in: app)
        XCTAssertTrue(save.isEnabled)
        saveScreenshot("09-Explicit-eligibility-self-report")
        // Inspect the gate without saving or contacting a service.
        app.navigationBars["Eligibility review"].buttons["Cancel"].tap()
    }
    func testTrackerRetainsApplicationWhenJobIsOutsideBootstrap() {
        let app = XCUIApplication(); app.launchArguments = ["--preview", "--uitesting", "--preview-missing-job"]; app.launch()
        XCTAssertTrue(app.tabBars.buttons["Tracker"].waitForExistence(timeout: 10))
        app.tabBars.buttons["Tracker"].tap()
        let card = app.buttons["tracker.application.preview-application"]
        for _ in 0..<3 { if app.staticTexts["Tracked opportunity"].isHittable { break }; app.swipeUp() }
        XCTAssertTrue(app.staticTexts["Tracked opportunity"].exists)
        XCTAssertTrue(app.staticTexts["Documents reviewed. Application has not been submitted."].exists)
        saveScreenshot("10-Tracker-missing-job-placeholder")
        if card.exists { card.tap() } else { app.staticTexts["Tracked opportunity"].tap() }
        XCTAssertTrue(app.buttons["job.retryDetail"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Your application is still tracked: Ready."].exists)
        XCTAssertFalse(app.buttons["Prepare my application"].isEnabled)
        app.buttons["job.retryDetail"].tap()
        XCTAssertTrue(app.staticTexts["Your application is still tracked: Ready."].waitForExistence(timeout: 5))
        saveScreenshot("11-Tracker-job-detail-retry")
    }
    func testPreviewBannerClearsNavigationAtAccessibilityTextSize() {
        let app = XCUIApplication()
        app.launchArguments = ["--preview", "--uitesting", "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        XCTAssertTrue(app.tabBars.buttons["Discover"].waitForExistence(timeout: 10))
        app.tabBars.buttons["Discover"].tap()
        assertPreviewClearsNavigation(app, title: "Discover")
        XCTAssertTrue(app.navigationBars["Discover"].buttons["Add opportunity"].isHittable)
        saveScreenshot("12-Preview-accessibility-navigation")
    }
    func testRecoveryListsRestoreAfterRelaunchAndRecoverStoredBytes() {
        let app = XCUIApplication()
        app.launchArguments = ["--preview", "--uitesting", "--preview-recovery"]
        app.launch(); openRecovery(app)
        XCTAssertTrue(app.staticTexts["Stored-source.pdf"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["recovery.offline"].exists)
        saveScreenshot("13-Pending-recovery-before-relaunch")
        app.terminate() // Relaunch only; never uninstall or clear app data.
        app.launch(); openRecovery(app)
        let recover = app.buttons["recovery.recover.10000000-0000-4000-8000-000000000001"]
        scrollTo(recover, in: app)
        XCTAssertTrue(recover.isHittable); recover.tap()
        let removed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: recover)
        XCTAssertEqual(XCTWaiter.wait(for: [removed], timeout: 5), .completed)
        saveScreenshot("14-Stored-source-recovered-no-AI")
        let artifact = app.buttons["recovery.recover.20000000-0000-4000-8000-000000000001"]
        scrollTo(artifact, in: app); XCTAssertTrue(artifact.isHittable); artifact.tap()
        let artifactRemoved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: artifact)
        XCTAssertEqual(XCTWaiter.wait(for: [artifactRemoved], timeout: 5), .completed)
        saveScreenshot("15-Stored-artifact-recovered-no-AI")
        app.navigationBars["Saved file recovery"].buttons["Done"].tap()
        XCTAssertTrue(app.staticTexts["Recovered preview source"].waitForExistence(timeout: 5))
    }
    func testMissingBytesRequireConfirmedFreshSourceAndNeverDeleteOldOperation() {
        let app = XCUIApplication()
        app.launchArguments = ["--preview", "--uitesting", "--preview-recovery"]
        app.launch(); openRecovery(app)
        let missingID = "10000000-0000-4000-8000-000000000002"
        let recover = app.buttons["recovery.recover.\(missingID)"]
        scrollTo(recover, in: app); recover.tap()
        XCTAssertTrue(app.navigationBars["Saved file recovery"].exists, "Recovery errors must not dismiss the recovery sheet")
        XCTAssertFalse(app.alerts.firstMatch.exists, "Keep missing-byte recovery guidance inline")
        let fresh = app.buttons["recovery.fresh.\(missingID)"]
        scrollTo(fresh, in: app); XCTAssertTrue(fresh.isHittable)
        saveScreenshot("16-Missing-bytes-explicit-fresh-source")
        fresh.tap()
        let synthetic = app.buttons["recovery.syntheticSource"]
        XCTAssertTrue(synthetic.waitForExistence(timeout: 5)); synthetic.tap()
        let upload = app.buttons["recovery.uploadFresh"]
        XCTAssertFalse(upload.isEnabled)
        let confirm = app.switches["recovery.confirmFresh"]
        scrollTo(confirm, in: app); turnOn(confirm)
        scrollTo(upload, in: app); XCTAssertTrue(upload.isEnabled)
        saveScreenshot("17-Fresh-source-confirmation")
        upload.tap()
        XCTAssertTrue(app.navigationBars["Saved file recovery"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Missing-source.pdf"].exists)
        let noDeletion = app.staticTexts["recovery.noDeletion.10000000-0000-4000-8000-000000000003"]
        scrollTo(noDeletion, in: app); XCTAssertTrue(noDeletion.exists)
        XCTAssertFalse(app.buttons["recovery.recover.10000000-0000-4000-8000-000000000003"].exists)
        saveScreenshot("18-Old-operation-retained-no-deletion")
    }
    private func openRecovery(_ app: XCUIApplication) {
        XCTAssertTrue(app.tabBars.buttons["Studio"].waitForExistence(timeout: 10))
        app.tabBars.buttons["Studio"].tap()
        let recovery = app.buttons["studio.recovery"]
        XCTAssertTrue(recovery.waitForExistence(timeout: 5)); recovery.tap()
        XCTAssertTrue(app.navigationBars["Saved file recovery"].waitForExistence(timeout: 5))
    }
    func testNewPreparationRequiresCostAcknowledgmentAndDoesNotSurviveRelaunch() {
        let app = XCUIApplication(); app.launchArguments = ["--preview", "--uitesting", "--preview-recovery"]
        app.launch(); openRecovery(app)
        let id = "20000000-0000-4000-8000-000000000002"
        let recover = app.buttons["recovery.recover.\(id)"]
        scrollTo(recover, in: app); recover.tap()
        XCTAssertFalse(app.staticTexts["recovery.newPreparationAllowed.\(id)"].exists)
        let allow = app.buttons["recovery.allowNew.\(id)"]
        scrollTo(allow, in: app); allow.tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS 'paid AI/API credits'")).firstMatch.waitForExistence(timeout: 5))
        saveScreenshot("19-New-preparation-cost-warning")
        // The system may render Cancel as popover dismissal rather than a
        // button. The positive, cost-warned choice is the only authorizing tap.
        app.buttons["Allow one new preparation"].tap()
        let acknowledged = app.staticTexts["recovery.newPreparationAllowed.\(id)"]
        XCTAssertTrue(acknowledged.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Missing-application.pdf"].exists)
        saveScreenshot("20-New-attempt-acknowledged-no-AI-started")
        app.terminate(); app.launch(); openRecovery(app)
        XCTAssertFalse(acknowledged.exists)
        scrollTo(recover, in: app); XCTAssertTrue(recover.exists)
        XCTAssertFalse(app.buttons["recovery.allowNew.\(id)"].exists, "A new session must check missing bytes and acknowledge again")
    }
    private func scrollTo(_ element: XCUIElement, in app: XCUIApplication) {
        // iOS can report controls behind the sticky footer/navigation bar as
        // hittable. Bring the control into the unobscured content before tapping.
        let height = app.frame.height
        let top = (app.navigationBars.allElementsBoundByIndex.last?.frame.maxY ?? 0) + 12
        var bottom = app.frame.maxY - 20
        if app.keyboards.firstMatch.exists { bottom = min(bottom, app.keyboards.firstMatch.frame.minY - 12) }
        if app.tabBars.firstMatch.exists && app.tabBars.firstMatch.isHittable { bottom = min(bottom, app.tabBars.firstMatch.frame.minY - 12) }
        let footer = app.buttons["Prepare my application"]
        if footer.exists && footer.isHittable { bottom = min(bottom, footer.frame.minY - 12) }
        for _ in 0..<8 {
            let position = element.exists ? element.frame.midY : bottom + 200
            if position >= top && position <= bottom && element.isHittable { return }
            let up = position > bottom
            let distance = min(220, (bottom - top) * 0.6, abs(position - (up ? bottom : top)) + 40)
            let center = (top + bottom) / 2
            let high = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: (center - distance / 2) / height))
            let low = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: (center + distance / 2) / height))
            (up ? low : high).press(forDuration: 0.05, thenDragTo: up ? high : low)
        }
    }
    private func turnOn(_ toggle: XCUIElement) {
        // SwiftUI exposes the whole multiline row as a Switch. Its center is
        // the label, not the visible trailing switch control, on iOS 26.
        XCTAssertEqual(toggle.value as? String, "0", "Confirmation must start off")
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.93, dy: 0.5)).tap()
        let enabled = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == '1'"), object: toggle)
        XCTAssertEqual(XCTWaiter.wait(for: [enabled], timeout: 3), .completed)
    }
    private func assertPreviewClearsNavigation(_ app: XCUIApplication, title: String) {
        let banner = app.staticTexts["DESIGN PREVIEW · FICTIONAL DATA"].firstMatch
        let bar = app.navigationBars[title]
        XCTAssertTrue(banner.waitForExistence(timeout: 5)); XCTAssertTrue(bar.waitForExistence(timeout: 5))
        XCTAssertLessThanOrEqual(banner.frame.maxY, bar.frame.minY + 1, "Preview label must sit above navigation, never over it")
        XCTAssertLessThanOrEqual(app.buttons["Exit"].frame.maxY, bar.frame.minY + 1)
        for button in bar.buttons.allElementsBoundByIndex { XCTAssertTrue(button.isHittable) }
    }
    private func saveScreenshot(_ name: String) {
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot()); shot.name = name; shot.lifetime = .keepAlways; add(shot)
    }
}
