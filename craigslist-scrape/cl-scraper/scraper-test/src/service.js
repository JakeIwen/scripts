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
var core_1 = require('@angular/core');
var dynamic_component_1 = require('./dynamic.component');
var Service = (function () {
    function Service(factoryResolver) {
        this.factoryResolver = factoryResolver;
    }
    Service.prototype.setRootViewContainerRef = function (viewContainerRef) {
        this.rootViewContainer = viewContainerRef;
    };
    Service.prototype.addDynamicComponent = function () {
        var factory = this.factoryResolver.resolveComponentFactory(dynamic_component_1.DynamicComponent);
        var component = factory.create(this.rootViewContainer.parentInjector);
        this.rootViewContainer.insert(component.hostView);
    };
    Service = __decorate([
        core_1.Injectable(), 
        __metadata('design:paramtypes', [core_1.ComponentFactoryResolver])
    ], Service);
    return Service;
}());
exports.Service = Service;
//# sourceMappingURL=service.js.map