<?php

namespace App\Plugins\LibreSolarBms\Providers;

use App\Plugins\LibreSolarBms\Console\Commands\InstallPackage;
use Illuminate\Support\ServiceProvider;

class LibreSolarBmsServiceProvider extends ServiceProvider {
    public function boot(): void {
        $this->app->register(RouteServiceProvider::class);
        $this->commands([InstallPackage::class]);
    }
}
