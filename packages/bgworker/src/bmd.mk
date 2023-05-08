# Marked as a PHONY target because we insert the date and time when we build
# the package and thus we always want to generate the build-meta-data.xml to
# get the right timestamp
.PHONY: ../build-meta-data.xml

../build-meta-data.xml:
	export PKG_NAME=$$(xmlstarlet sel -N x=http://tail-f.com/ns/ncs-packages -t -v '/x:ncs-package/x:name' $$(ls ../package-meta-data.xml package-meta-data.xml.in 2>/dev/null | head -n 1)); \
	export PKG_VERSION=$$(xmlstarlet sel -N x=http://tail-f.com/ns/ncs-packages -t -v '/x:ncs-package/x:package-version' $$(ls ../package-meta-data.xml package-meta-data.xml.in 2>/dev/null | head -n 1)); \
	bash -c 'eval "cat <<< \"$$(<build-meta-data.xml.in)\""' > $@
