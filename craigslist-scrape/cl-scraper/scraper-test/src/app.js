"use strict";
var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
var __metadata = (this && this.__metadata) || function (k, v) {
    if (typeof Reflect === "object" && typeof Reflect.metadata === "function") return Reflect.metadata(k, v);
};
//our root app component
var core_1 = require('@angular/core');
var platform_browser_1 = require('@angular/platform-browser');
var service_1 = require('./service');
var dynamic_component_1 = require('./dynamic.component');
var App = (function () {
    //constructor(service: Service, viewContainerRef: ViewContainerRef) {
    function App(service) {
        this.name = 'from Angular';
        this.service = {};
        this.service = service;
    }
    App.prototype.ngOnInit = function () {
        this.service.setRootViewContainerRef(this.viewContainerRef);
        this.service.addDynamicComponent();
    };
    __decorate([
        core_1.ViewChild('dynamic', { read: core_1.ViewContainerRef }), 
        __metadata('design:type', core_1.ViewContainerRef)
    ], App.prototype, "viewContainerRef", void 0);
    App = __decorate([
        core_1.Component({
            selector: 'my-app',
            template: "\n    <div>\n      <h2>Hello {{name}}</h2>\n      <template #dynamic></template>\n    </div>\n  ",
        }), 
        __metadata('design:paramtypes', [service_1.Service])
    ], App);
    return App;
}());
exports.App = App;
var AppModule = (function () {
    function AppModule() {
    }
    AppModule = __decorate([
        core_1.NgModule({
            imports: [platform_browser_1.BrowserModule],
            declarations: [
                App,
                dynamic_component_1.DynamicComponent
            ],
            providers: [service_1.Service],
            entryComponents: [dynamic_component_1.DynamicComponent],
            bootstrap: [App]
        }), 
        __metadata('design:paramtypes', [])
    ], AppModule);
    return AppModule;
}());
exports.AppModule = AppModule;
//# sourceMappingURL=app.js.map