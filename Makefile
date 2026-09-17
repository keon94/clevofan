name := clevofan
obj-m := $(name).o

KVERSION ?= $(shell uname -r)
KDIR ?= /lib/modules/$(KVERSION)/build
PWD := $(shell pwd)

ifdef DEBUG
CFLAGS_$(obj-m) := -DDEBUG
endif

default:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

load:
	-rmmod $(name)
	insmod $(name).ko

install:
	install -D -m 0644 $(name).ko \
		/lib/modules/$(KVERSION)/extra/$(name)/$(name).ko
	depmod -a $(KVERSION)

uninstall:
	rm -f /lib/modules/$(KVERSION)/extra/$(name)/$(name).ko
	depmod -a $(KVERSION)
