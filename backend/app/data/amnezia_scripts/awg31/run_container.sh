# Run container
sudo docker run -d \
--log-driver none \
--restart always \
--privileged \
--cap-add=NET_ADMIN \
--cap-add=SYS_MODULE \
-p $AWG_SERVER_PORT:$AWG_SERVER_PORT/udp \
-v /lib/modules:/lib/modules \
--sysctl="net.ipv4.conf.all.src_valid_mark=1" \
--name $CONTAINER_NAME \
$CONTAINER_NAME

sudo docker network connect amnezia-dns-net $CONTAINER_NAME
