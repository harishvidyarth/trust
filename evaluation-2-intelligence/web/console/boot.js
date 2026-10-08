(function () {
  var C = window.C;
  var params = new URLSearchParams(location.search);
  C.params = { sample: params.get("sample"), step: params.get("step"), select: params.get("select"), tab: params.get("tab") };

  C.$("helpBtn").addEventListener("click", C.showHelp);
  C.$("dialog").addEventListener("click", function (event) {
    if (event.target === C.$("dialog")) C.$("dialog").close();
  });

  async function start() {
    C.syncThemeButton();
    if (params.get("demo") === "1") C.enterDemo("");
    var role = params.get("as");
    if (C.state.demo && role) {
      if (await C.login(role, C.demo.password)) return;
    }
    if (C.state.demo) {
      C.renderShell();
      C.showLogin("");
      return;
    }
    try {
      var me = await C.api("GET", "/v1/auth/me");
      C.state.me = { username: me.username, role: me.role };
      C.state.csrf = me.csrf_token || me.csrf || null;
      C.renderShell();
      C.go(C.homeFor(me.role));
    } catch (error) {
      C.renderShell();
      if (error.network) {
        C.enterDemo("The service at " + C.base() + " could not be reached, so this is sample data.");
        C.showLogin("");
        C.status("Service not reachable. Showing demo data.");
      } else {
        C.showLogin(error.status === 401 ? "Please sign in to continue." : C.friendly(error));
      }
    }
  }
  start();
})();
