import {
  ComponentFactoryResolver,
  Injectable,
  Inject,
  ReflectiveInjector
} from '@angular/core'

import { DynamicComponent } from './dynamic.component'

@Injectable()
export class Service {

  constructor(private factoryResolver: ComponentFactoryResolver) { }

  public setRootViewContainerRef(viewContainerRef) {
    this.rootViewContainer = viewContainerRef
  }

  public addDynamicComponent() {
    const factory = this.factoryResolver.resolveComponentFactory(DynamicComponent)
    const component = factory.create(this.rootViewContainer.parentInjector)
    
    this.rootViewContainer.insert(component.hostView)
  }

}
