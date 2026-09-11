#include <linux/module.h>
#include <linux/export-internal.h>
#include <linux/compiler.h>

MODULE_INFO(name, KBUILD_MODNAME);

__visible struct module __this_module
__section(".gnu.linkonce.this_module") = {
	.name = KBUILD_MODNAME,
	.init = init_module,
#ifdef CONFIG_MODULE_UNLOAD
	.exit = cleanup_module,
#endif
	.arch = MODULE_ARCH_INIT,
};


MODULE_INFO(depends, "");

MODULE_ALIAS("dmi*:rn*W35_37ET*:");
MODULE_ALIAS("dmi*:rn*W350SS*:");
MODULE_ALIAS("dmi*:rn*P170SM-A*:");
MODULE_ALIAS("dmi*:rn*P65xHP*:");

MODULE_INFO(srcversion, "162EAEF1AB2176B0810810E");
